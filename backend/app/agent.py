"""The agent: one-click multi-question sessions with pause / resume / skip / stop and crash recovery."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from .bridge import BridgeError, DashboardHub, ExtensionBridge
from .config import Settings
from .db import DB
from .engine import generate_solution, pick_language, repair_solution, research
from .llm import LLMError, LLMProvider
from .models import CheckResult, Question, QStatus, SessionConfig, SessionState, SolutionDraft, ValidationReport
from .sandbox import Sandbox
from .search import SearchProvider
from .validator import validate

log = logging.getLogger("katapilot.agent")


class PauseSession(Exception):
    """Recoverable condition that needs the user (session expired, extension gone, navigation blocked)."""


class QuestionFailed(Exception):
    pass


class Agent:
    def __init__(self, s: Settings, db: DB, hub: DashboardHub, bridge: ExtensionBridge,
                 llm: LLMProvider | None, search: SearchProvider | None, sandbox: Sandbox):
        self.s, self.db, self.hub, self.bridge = s, db, hub, bridge
        self.llm, self.search, self.sandbox = llm, search, sandbox
        self.session_id: int | None = None
        self.config: SessionConfig | None = None
        self.state = SessionState.idle
        self.current_qid: int | None = None
        self._task: asyncio.Task | None = None
        self._q_task: asyncio.Task | None = None
        self._resume = asyncio.Event()
        self._pause_requested = False
        self._skip_requested = False
        self._last_inserted: tuple[int, str] | None = None   # (qid, code) currently in the GUVI editor

    # ------------------------------------------------------------------ public controls
    def snapshot(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "state": self.state.value, "current_question_id": self.current_qid,
                "config": self.config.model_dump() if self.config else None}

    async def start(self, cfg: SessionConfig) -> int:
        if self._task and not self._task.done():
            raise RuntimeError("A session is already active. Stop it first.")
        if not self.llm:
            raise RuntimeError("No LLM provider is configured. Set ANTHROPIC_API_KEY (or another provider) in backend/.env.")
        if not self.bridge.connected:
            raise RuntimeError("The Chrome extension is not connected. Open Chrome with KataPilot installed and a GUVI tab open.")
        self.config = cfg
        self.session_id = await self.db.create_session(cfg.module, cfg.model_dump())
        self._task = asyncio.create_task(self._run(fresh=True))
        return self.session_id

    async def resume_interrupted(self, sid: int) -> None:
        """Continue a session that was interrupted by a backend restart."""
        if self._task and not self._task.done():
            raise RuntimeError("A session is already active.")
        row = await self.db.get_session(sid)
        if not row:
            raise RuntimeError("Session not found")
        self.session_id, self.config = sid, SessionConfig(**json.loads(row["config"]))
        await self.db.set_session_state(sid, "running")
        self._task = asyncio.create_task(self._run(fresh=False))

    async def pause(self) -> None:
        if self.state == SessionState.running:
            self._pause_requested = True
            await self._activity("Pausing after the current step…")

    async def resume(self) -> None:
        if self.state in (SessionState.paused, SessionState.waiting_review):
            self._pause_requested = False
            self._resume.set()
        elif self.state == SessionState.running:
            self._pause_requested = False

    async def skip(self) -> None:
        if self.state == SessionState.waiting_review:
            self._resume.set()   # the reviewed question is finished; just move on
            return
        if self.current_qid is not None:
            self._skip_requested = True
            if self._q_task and not self._q_task.done():
                self._q_task.cancel()
            self._resume.set()

    async def stop(self) -> None:
        if self._task and not self._task.done():
            await self._set_state(SessionState.stopping)
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    # ------------------------------------------------------------------ internals
    async def _set_state(self, state: SessionState, error: str | None = None) -> None:
        self.state = state
        if self.session_id:
            await self.db.set_session_state(self.session_id, state.value, error)
        await self.hub.emit("session", **self.snapshot(), error=error)

    async def _log(self, level: str, message: str, step: str | None = None) -> None:
        row = await self.db.log(level, message, self.session_id, self.current_qid, step)
        await self.hub.emit("log", **row)

    async def _activity(self, message: str, step: str | None = None) -> None:
        if self.current_qid:
            await self.db.update_question(self.current_qid, activity=message[:200])
        await self.hub.emit("activity", question_id=self.current_qid, step=step, message=message)
        await self._log("info", message, step)

    async def _q(self, qid: int, **fields) -> None:
        if "status" in fields and isinstance(fields["status"], QStatus):
            fields["status"] = fields["status"].value
        await self.db.update_question(qid, **fields)
        row = await self.db.get_question(qid)
        await self.hub.emit("question", **(row or {}))

    async def _checkpoint(self) -> None:
        """Cooperative pause point between steps."""
        if self._pause_requested:
            self._pause_requested = False
            await self._set_state(SessionState.paused)
            await self._wait_for_resume()

    async def _wait_for_resume(self) -> None:
        self._resume.clear()
        await self._resume.wait()
        await self._set_state(SessionState.running)

    async def _call(self, method: str, params: dict | None = None, timeout: float = 30, retries: int = 1) -> Any:
        for attempt in range(retries + 1):
            try:
                return await self.bridge.call(method, params, timeout)
            except BridgeError as e:
                if e.code == "extension_disconnected":
                    raise PauseSession(str(e)) from e
                if e.code == "login_required":
                    raise PauseSession("Your GUVI session appears to have expired. Sign in to GUVI in the agent tab, then press Resume.") from e
                if e.code == "navigation_blocked":
                    raise PauseSession(str(e)) from e
                if e.code == "tab_closed":
                    raise PauseSession("The GUVI tab the agent was using was closed. Open a GUVI tab and press Resume.") from e
                if attempt == retries:
                    raise
                await asyncio.sleep(1.5)

    # ------------------------------------------------------------------ session loop
    async def _run(self, fresh: bool) -> None:
        assert self.config and self.session_id
        cfg = self.config
        await self._set_state(SessionState.running)
        try:
            while True:
                try:
                    if fresh:
                        await self._discover(cfg)
                        fresh = False
                    await self._process_queue(cfg)
                    break
                except PauseSession as e:
                    await self._log("warn", str(e), "paused")
                    await self._set_state(SessionState.paused, str(e))
                    await self._wait_for_resume()
            await self._set_state(SessionState.completed)
            await self._log("info", "Session complete.")
        except asyncio.CancelledError:
            if self.current_qid:
                await self._q(self.current_qid, status=QStatus.pending, activity="Stopped by user")
            self.current_qid = None
            await self._set_state(SessionState.stopped)
            await self._log("info", "Session stopped by user.")
        except Exception as e:
            log.exception("session crashed")
            await self._set_state(SessionState.failed, str(e))
            await self._log("error", f"Session failed: {e}")
        finally:
            self.current_qid = None

    async def _discover(self, cfg: SessionConfig) -> None:
        await self.hub.emit("activity", question_id=None, step="detect", message="Detecting GUVI page")
        tab = await self._call("tab.claim", {})
        await self._log("info", f"Using GUVI tab: {tab.get('title', '')} ({tab.get('url', '')})", "detect")
        page = await self._call("page.detect", {"module": cfg.module})
        if page.get("loginRequired"):
            raise PauseSession("GUVI shows a sign-in page. Sign in to GUVI in that tab, then press Resume.")
        if page.get("module") and page["module"] != cfg.module and cfg.discovery != "urls":
            await self._log("warn", f"The open page looks like {page['module']}, but the session module is {cfg.module}.", "detect")

        items: list[dict] = []
        if cfg.discovery == "urls":
            items = [{"url": u.strip(), "title": u.strip()} for u in cfg.urls if u.strip().startswith("https://")]
        elif cfg.discovery == "current":
            if page.get("pageType") != "question":
                raise RuntimeError("The current GUVI tab is not showing a question. Open a question or use listing discovery.")
            items = [{"url": page["url"], "title": page.get("title") or "Current question"}]
        else:
            await self.hub.emit("activity", question_id=None, step="discover", message="Discovering questions on this page")
            res = await self._call("page.discover", {"module": cfg.module, "max": cfg.max_questions}, timeout=60)
            items = res.get("items", [])
            if not items and page.get("pageType") == "question":
                items = [{"url": page["url"], "title": page.get("title") or "Current question"}]
            await self._log("info", f"Discovered {len(items)} question(s) via {res.get('strategy', 'heuristics')}.", "discover")
        if cfg.skip_solved_on_guvi and cfg.discovery == "listing":
            unsolved = [it for it in items if not it.get("solvedOnGuvi")]
            if unsolved and len(unsolved) < len(items):
                await self._log("info", f"Skipping {len(items) - len(unsolved)} question(s) GUVI already marks as solved.", "discover")
                items = unsolved
        if not items:
            raise RuntimeError("No questions found. Open a GUVI question list (or a question) in the agent tab and try again, "
                               "or use 'Inspect page' in Settings to tune the adapter.")
        seen: set[str] = set()
        pos = 0
        for it in items[: cfg.max_questions]:
            key = it.get("url") or json.dumps(it.get("locator"))
            if key in seen:
                continue
            seen.add(key)
            qid = await self.db.add_question(self.session_id, pos, cfg.module, it.get("url"), it.get("title") or f"Question {pos + 1}",
                                             it.get("locator"), it.get("difficulty"))
            pos += 1
            await self.hub.emit("question", **(await self.db.get_question(qid)))
        await self._log("info", f"Queued {pos} question(s).", "queue")

    async def _process_queue(self, cfg: SessionConfig) -> None:
        while True:
            rows = await self.db.session_questions(self.session_id)
            nxt = next((r for r in rows if r["status"] == "pending"), None)
            if not nxt:
                return
            await self._checkpoint()
            self.current_qid = nxt["id"]
            self._skip_requested = False
            self._q_task = asyncio.create_task(self._process_question(nxt, cfg))
            try:
                await self._q_task
            except asyncio.CancelledError:
                if self._skip_requested:
                    await self._q(nxt["id"], status=QStatus.skipped, activity="Skipped by user")
                    await self._log("info", "Question skipped.")
                    if self.state == SessionState.waiting_review:
                        await self._set_state(SessionState.running)
                else:
                    raise
            except PauseSession:
                await self._q(nxt["id"], status=QStatus.pending, activity="Waiting for you — see session message")
                raise
            except QuestionFailed as e:
                await self._q(nxt["id"], status=QStatus.failed, error=str(e)[:1000], activity="Failed")
                await self._log("error", f"Failed: {e}")
            except LLMError as e:
                await self._log("error", f"AI error: {e}")
                if "API key" in str(e):
                    # Keys exhausted or rejected: keep the question queued and wait for the user instead of failing the rest.
                    await self._q(nxt["id"], status=QStatus.pending, activity="Waiting - all API keys are exhausted")
                    raise PauseSession(str(e))
                await self._q(nxt["id"], status=QStatus.failed, error=str(e)[:1000], activity="AI generation failed")
            except BridgeError as e:
                await self._q(nxt["id"], status=QStatus.failed, error=str(e), activity="Browser action failed")
                await self._log("error", f"Browser error: {e}")
            finally:
                self.current_qid = None
                self._q_task = None

    # ------------------------------------------------------------------ per question
    async def preserve_editor(self) -> None:
        """Before leaving a page, keep a copy of anything the user typed over our inserted code."""
        if not self._last_inserted:
            return
        qid, code = self._last_inserted
        try:
            cur = await self.bridge.call("editor.read", {}, 10)
        except BridgeError:
            return
        content = (cur or {}).get("content")
        if content and content.strip() != code.strip():
            await self.db.add_backup(qid, None, "user_edit_before_navigation", content)
            await self._log("info", "Saved your edits from the editor before navigating away (see History → backups).")
        self._last_inserted = None

    @staticmethod
    def _same_title(a: str, b: str) -> bool:
        na, nb = (re.sub(r"[^a-z0-9]", "", re.sub(r"^\s*\d+\s*[.)]", "", x or "").lower()) for x in (a, b))
        return bool(na and nb) and (na == nb or na in nb or nb in na)

    async def open_question(self, row: dict) -> dict:
        """Open by URL when there is one; fall back to clicking the row in the list when a guessed URL misses."""
        await self.preserve_editor()
        locator = json.loads(row["locator"]) if row.get("locator") else None
        wait = {"module": row["module"], "timeoutMs": 25000, "dismissOverlays": True}
        if row.get("url"):
            await self._call("tab.navigate", {"url": row["url"]}, timeout=45)
            ready = await self._call("page.waitReady", wait, timeout=40)
            if ready.get("loginRequired"):
                raise PauseSession("GUVI is asking you to sign in. Sign in in the agent tab, then press Resume.")
            guessed = bool(locator and locator.get("guessed"))
            hit = ready.get("pageType") == "question" and (not guessed or self._same_title(ready.get("title", ""), row.get("title", "")))
            if hit or not locator:
                return ready
            await self._log("warn", "The derived address did not open this question; opening it from the list instead.", "open")
        if not locator:
            raise QuestionFailed("Question has neither a URL nor a locator.")
        await self._call("page.openItem", {"locator": locator}, timeout=75, retries=0)
        ready = await self._call("page.waitReady", wait, timeout=40)
        if ready.get("loginRequired"):
            raise PauseSession("GUVI is asking you to sign in. Sign in in the agent tab, then press Resume.")
        return ready

    async def _process_question(self, row: dict, cfg: SessionConfig) -> None:
        qid = row["id"]
        prefs = await self.db.get_prefs()
        await self._q(qid, status=QStatus.processing, error=None)
        await self._activity("Opening question", "open")
        ready = await self.open_question(row)
        if ready.get("pageType") != "question":
            raise QuestionFailed("Page did not show a question after loading (editor or statement not found).")
        await self._checkpoint()

        await self._activity("Extracting question", "extract")
        overrides = (prefs.get("selector_overrides") or {}).get(cfg.module) or {}
        data = await self._call("page.extract", {"module": cfg.module, "overrides": overrides}, timeout=30)
        q = Question(**{**data, "module": cfg.module})
        if not q.statement.strip() and len(q.raw_text) < 80:
            raise QuestionFailed("Could not find the problem statement on the page.")
        for w in q.extraction_warnings:
            await self._log("warn", f"Extraction: {w}", "extract")
        await self._q(qid, title=q.title or row["title"], url=q.url or row.get("url"), fingerprint=q.fingerprint,
                      difficulty=q.difficulty or row.get("difficulty"), question_json=q.model_dump_json())

        # Duplicates: same problem already handled in this session, or already prepared earlier.
        others = [r for r in await self.db.session_questions(self.session_id) if r["id"] != qid]
        if any(r["fingerprint"] == q.fingerprint and r["status"] in ("code_ready", "needs_review") for r in others):
            await self._q(qid, status=QStatus.skipped, activity="Duplicate of another question in this session")
            return
        if cfg.skip_already_prepared and q.url:
            prev = await self.db.prepared_for_url(q.url)
            # Only a question that was actually submitted before counts as done; prepared-but-unsubmitted ones are redone.
            if prev and prev["id"] != qid and (prev.get("activity") or "").startswith("Submitted - GUVI accepted"):
                await self._q(qid, status=QStatus.skipped, activity="Already submitted in an earlier session")
                return

        language = pick_language(q, cfg.language, prefs.get("preferred_language", "cpp"))

        refs = []
        if cfg.use_web_search and self.search:
            await self._q(qid, status=QStatus.researching)
            await self._activity("Searching the web for matching problems", "research")
            refs = await research(q, self.search, self.llm, lambda m: self._activity(m, "research"),
                                  self.s.search_max_results, self.s.search_fetch_pages)
            eq = [r for r in refs if r.equivalent]
            await self._log("info", f"Research: {len(refs)} result(s), {len(eq)} judged equivalent.", "research")
        await self._checkpoint()

        parts_payload: list[dict] = []
        if q.parts and q.module == "sqlkata":
            # GUVI SQLKata: one scenario, several sub-questions, each with its own editor buffer.
            results = []
            for part in q.parts:
                await self._checkpoint()
                pq = q.model_copy(update={"statement": f"{q.statement}\n\nTASK - {part.title}: {part.text}", "parts": []})
                d, _, rep, att = await self._solve(qid, pq, refs, language, f" ({part.title} of {len(q.parts)})")
                results.append((part, d, rep, att))
            draft, report, attempt = self._combine_parts(results)
            extra: list[dict] = []
            parts_payload = [{"index": p.index, "title": p.title, "task": p.text, "code": d.code, "verdict": r.verdict}
                             for p, d, r, _ in results]
        else:
            draft, extra, report, attempt = await self._solve(qid, q, refs, language)

        await self.db.add_solution(qid, attempt, draft.language, draft.code,
                                   draft.model_dump(exclude={"code"}) | {"extra_tests": extra, "parts": parts_payload},
                                   [r.model_dump() for r in refs], report.model_dump())
        await self.hub.emit("solution", question_id=qid)
        await self._checkpoint()

        inserted, note = False, None
        # In auto-submit mode GUVI's own Run is the judge, so a local failure does not block insertion.
        if cfg.review_mode != "prepare_only" and (report.verdict != "failed" or cfg.review_mode == "auto_submit"):
            await self._q(qid, status=QStatus.inserting)
            await self._activity("Inserting solution into the GUVI editor", "insert")
            inserted, note = await self.insert_solution(qid, q.url, draft.code, draft.language, q.module, parts_payload)

        if cfg.review_mode == "auto_submit" and inserted:
            done = await self._run_and_submit(qid, q, draft, parts_payload)
            if done is not None:
                status, act = done
                await self._q(qid, status=status, inserted=1, activity=act)
                await self._log("info", f"{(q.title or row['title'])[:60]}: {act}.", "done")
                return

        tail = " and inserted" if inserted else (f"; not inserted ({note})" if note else "")
        if report.verdict == "passed":
            status, act = QStatus.code_ready, "Code ready - validated on samples" + tail
        elif report.verdict == "inconclusive":
            why = ("ran on the sample tables, GUVI shows no expected output to compare" if q.module == "sqlkata"
                   else "could not fully validate locally")
            status, act = QStatus.needs_review, f"Needs review - {why}" + tail
        else:
            status, act = QStatus.needs_review, "Needs review - validation still failing after repairs (not inserted)"
        await self._q(qid, status=status, inserted=int(inserted), activity=act)
        await self._log("info", f"{(q.title or row['title'])[:60]}: {act}.", "done")

        if cfg.review_mode == "review_each":
            await self._set_state(SessionState.waiting_review)
            await self.hub.emit("activity", question_id=qid, step="review", message="Waiting for your review - press Resume for the next question")
            self._resume.clear()
            await self._resume.wait()
            if self.state == SessionState.waiting_review:
                await self._set_state(SessionState.running)

    async def _run_and_submit(self, qid: int, q: Question, draft: SolutionDraft, parts: list[dict]):
        """Auto-submit mode: Run on GUVI; submit only when GUVI reports every test case as passed.
        A failing Run gets one repair round using GUVI's own output. Returns (status, activity) or None."""
        if parts:
            await self._log("info", "Auto-submit is not used for multi-part SQL problems yet; the queries are inserted for you to run.", "submit")
            return None
        try:
            limit = max(1, self.s.max_guvi_fix_attempts)
            history: list[str] = []
            fixes = 0
            while True:
                await self._checkpoint()
                await self._activity(f"Running the code on GUVI (try {fixes + 1})", "validate")
                run = await self._call("page.run", {}, timeout=90, retries=0)
                stage, detail = "Run Code", (run.get("text") or "")[:1800]
                verdict = run.get("verdict")
                if verdict == "unknown":
                    return QStatus.needs_review, "Needs review - could not read GUVI's Run result, so it was not submitted"
                if verdict == "passed":
                    await self._activity("All visible test cases passed on GUVI; submitting", "insert")
                    sub = await self._call("page.submit", {}, timeout=90, retries=0)
                    self._last_inserted = None
                    await self._log("info", f"GUVI after submit [{sub.get('verdict')}, badge: {sub.get('badge')}]: "
                                            f"{' '.join((sub.get('text') or '').split())[:300]}", "submit")
                    if sub.get("verdict") == "passed":
                        return QStatus.code_ready, "Submitted - GUVI accepted it"
                    # Not confirmed as accepted: GUVI's hidden test cases rejected it. Keep fixing.
                    stage = "Submit (hidden test cases)"
                    detail = ("The code passed the visible sample cases but GUVI REJECTED the submission on its hidden test cases. "
                              "Think about edge cases: empty input, single element, duplicates, value at the head/tail, negative "
                              "numbers, large values, exact output wording and spacing.\n" + (sub.get("text") or ""))[:1800]
                if fixes >= limit:
                    return QStatus.needs_review, f"Needs review - GUVI still rejects it after {limit} rewrites ({stage})"
                fixes += 1
                await self._log("warn", f"GUVI {stage} failed (rewrite {fixes} of {limit}):\n{detail[:600]}", "validate")
                await self._activity(f"GUVI rejected it at {stage}; rewriting the code ({fixes} of {limit})", "repair")
                history.append(f"--- Attempt {fixes} code ---\n{draft.code[:2500]}\n--- GUVI result ({stage}) ---\n{detail}")
                checks = [CheckResult(name=f"GUVI {stage} result (the real judge)", status="fail", detail=detail)]
                if len(history) > 1:
                    checks.append(CheckResult(name="Earlier failed attempts - do NOT repeat these; rethink the I/O format and logic",
                                              status="fail", detail="\n\n".join(history[:-1])[-3500:]))
                report = ValidationReport(verdict="failed", sandbox="guvi", checks=checks)
                draft, _ = await repair_solution(q, draft, report, self.llm)
                await self.db.add_solution(qid, fixes + 1, draft.language, draft.code,
                                           draft.model_dump(exclude={"code"}) | {"extra_tests": [], "parts": []}, [], report.model_dump())
                await self.hub.emit("solution", question_id=qid)
                if not await self._write(qid, q.url, draft.code, "before_fix_insert"):
                    return QStatus.needs_review, "Needs review - could not re-insert the rewritten code"
                self._last_inserted = (qid, draft.code)
        except BridgeError as e:
            await self._log("warn", f"Auto-submit stopped: {e}", "submit")
            return QStatus.needs_review, f"Needs review - inserted, but not submitted ({e})"

    async def _solve(self, qid: int, q: Question, refs: list, language: str, label: str = ""):
        """Generate, validate, and repair up to MAX_REPAIR_ATTEMPTS times."""
        await self._q(qid, status=QStatus.generating)
        await self._activity(f"Analyzing problem and generating {language} solution{label}", "generate")
        draft, extra = await generate_solution(q, refs, self.llm, language)
        attempt = 1
        while True:
            await self._q(qid, status=QStatus.validating)
            await self._activity(f"Validating attempt {attempt}{label}", "validate")
            report = await validate(q, draft, self.sandbox, extra)
            if report.verdict != "failed" or attempt > self.s.max_repair_attempts:
                return draft, extra, report, attempt
            await self._activity(f"Validation failed; repairing{label} (attempt {attempt + 1})", "repair")
            await self._log("warn", report.failure_summary[:1500], "validate")
            draft, extra = await repair_solution(q, draft, report, self.llm)
            attempt += 1

    @staticmethod
    def _combine_parts(results: list) -> tuple[SolutionDraft, ValidationReport, int]:
        def join(attr: str) -> str:
            return "\n\n".join(f"{p.title}: {getattr(d, attr)}" for p, d, _, _ in results)

        verdicts = [r.verdict for _, _, r, _ in results]
        verdict = "failed" if "failed" in verdicts else "passed" if all(v == "passed" for v in verdicts) else "inconclusive"
        checks = [CheckResult(name=f"{p.title} · {c.name}", status=c.status, detail=c.detail) for p, _, r, _ in results for c in r.checks]
        draft = SolutionDraft(
            understanding=join("understanding"), approach=join("approach"), algorithm_explanation=join("algorithm_explanation"),
            time_complexity=results[0][1].time_complexity, space_complexity=results[0][1].space_complexity,
            edge_cases=[e for _, d, _, _ in results for e in d.edge_cases][:8], language="sql",
            code="\n\n".join(f"-- {p.title}\n{d.code.strip()}" for p, d, _, _ in results) + "\n")
        return draft, ValidationReport(verdict=verdict, checks=checks, sandbox="sqlite"), max(a for *_, a in results)

    async def _write(self, qid: int, url: str | None, code: str, reason: str) -> bool:
        cur = await self._call("editor.read", {}, timeout=15)
        await self.db.add_backup(qid, url, reason, (cur or {}).get("content") or "")
        res = await self._call("editor.write", {"code": code}, timeout=20)
        if res.get("verified"):
            await self._log("info", f"Inserted into the {res.get('editor', 'editor')} editor and verified. Submitting is up to you.", "insert")
            return True
        await self._log("warn", f"Wrote to the {res.get('editor')} editor, but its content did not match afterwards.", "insert")
        return False

    async def insert_solution(self, qid: int, url: str | None, code: str, language: str, module: str,
                              parts: list[dict] | None = None) -> tuple[bool, str | None]:
        """Back up -> switch language if needed -> select sub-question -> write -> verify. Never runs or submits."""
        try:
            if module == "codekata" and language in ("cpp", "c", "java", "python", "javascript"):
                det = await self._call("page.detect", {"module": module}, timeout=15)
                current = (det.get("editor") or {}).get("language")
                if current and current != language:
                    before = await self._call("editor.read", {}, timeout=15)
                    await self.db.add_backup(qid, url, f"before_language_switch_from_{current}", (before or {}).get("content") or "")
                    await self._activity(f"Switching GUVI's editor from {current} to {language}", "insert")
                    await self._call("editor.setLanguage", {"language": language}, timeout=25, retries=0)
            if parts:
                ok = True
                for part in parts:
                    await self._call("editor.selectPart", {"index": part["index"]}, timeout=15, retries=0)
                    ok = await self._write(qid, url, part["code"], f"before_insert_{part['title']}".replace(" ", "_")) and ok
                self._last_inserted = None   # several buffers; nothing single to compare later
                return ok, None if ok else "one of the sub-questions could not be verified"
            ok = await self._write(qid, url, code, "before_insert")
            if ok:
                self._last_inserted = (qid, code)
            return ok, None if ok else "the editor content did not match after writing"
        except BridgeError as e:
            await self._log("warn", f"Could not insert automatically: {e}", "insert")
            return False, str(e)

    async def manual_insert(self, qid: int) -> dict:
        """'Insert Into GUVI' button: open the question if needed, then insert the latest solution."""
        if self._task and not self._task.done() and self.state == SessionState.running:
            raise RuntimeError("Pause the session before inserting manually.")
        row = await self.db.get_question(qid)
        sol = await self.db.latest_solution(qid)
        if not row or not sol:
            raise RuntimeError("No solution for this question yet.")
        status = await self._call("tab.claim", {})
        if row.get("url") and status.get("url", "").split("?")[0].rstrip("/") != row["url"].split("?")[0].rstrip("/"):
            ready = await self.open_question(row)
            if ready.get("pageType") != "question":
                raise RuntimeError("Could not open this question in the GUVI tab.")
        ok, note = await self.insert_solution(qid, row.get("url"), sol["code"], sol["language"], row["module"],
                                              (sol.get("analysis") or {}).get("parts") or None)
        if ok:
            await self._q(qid, inserted=1)
        return {"inserted": ok, "note": note}
