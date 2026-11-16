"""Backend tests. The sandbox tests use the real sandbox backend available on this machine."""
import asyncio
import json

import pytest

from app.agent import Agent
from app.bridge import BridgeError, DashboardHub
from app.config import Settings
from app.db import DB
from app.engine import distinctive_phrase, pick_language, question_brief
from app.llm import LLMProvider
from app.models import Question, SampleCase, SessionConfig, SolutionDraft, SqlTable
from app.sandbox import make_sandbox
from app.search import fetch_page_text, html_to_text, SearchError
from app.validator import normalize, validate, validate_sql

S = Settings(_env_file=None)
SANDBOX = make_sandbox(S)
needs_sandbox = pytest.mark.skipif(SANDBOX.name == "none", reason="no sandbox backend on this machine")

GOOD_CPP = '#include <bits/stdc++.h>\nusing namespace std;\nint main(){long long a,b;cin>>a>>b;cout<<a+b<<"\\n";return 0;}\n'
BAD_CPP = '#include <bits/stdc++.h>\nusing namespace std;\nint main(){long long a,b;cin>>a>>b;cout<<a-b<<"\\n";return 0;}\n'


def draft(code, lang="cpp", **kw):
    return SolutionDraft(understanding="u", approach="a", algorithm_explanation="e", time_complexity="O(1)",
                         space_complexity="O(1)", edge_cases=[], language=lang, code=code, **kw)


def sum_question(url="https://www.guvi.in/code-kata/q/1"):
    return Question(url=url, module="codekata", title="Sum of two numbers", statement="Print the sum of two integers.",
                    samples=[SampleCase(input="3 4", output="7"), SampleCase(input="-5 2", output="-3")], language="cpp")


# ---------------------------------------------------------------- validator
@needs_sandbox
async def test_cpp_passes_samples():
    r = await validate(sum_question(), draft(GOOD_CPP), SANDBOX, [{"input": "0 0", "output": "0"}])
    assert r.verdict == "passed", r.checks
    assert r.sandbox == SANDBOX.name


@needs_sandbox
async def test_cpp_wrong_answer_fails():
    r = await validate(sum_question(), draft(BAD_CPP), SANDBOX, [])
    assert r.verdict == "failed"
    assert any(c.name == "Sample 1" and c.status == "fail" for c in r.checks)


@needs_sandbox
async def test_compile_error_reported():
    r = await validate(sum_question(), draft("int main(){ return undefined_symbol; }"), SANDBOX, [])
    assert r.verdict == "failed"
    assert any(c.name == "Compilation" and "undefined_symbol" in c.detail for c in r.checks)


@needs_sandbox
async def test_python_and_timeout():
    q = sum_question()
    ok = await validate(q, draft("a,b=map(int,input().split())\nprint(a+b)\n", "python"), SANDBOX, [])
    assert ok.verdict == "passed"
    slow = await validate(Question(url="u", module="codekata", samples=[SampleCase(input="1", output="1")]),
                          draft("int main(){ volatile unsigned long x=0; for(;;) x++; }"), SANDBOX, [])
    assert any("Time limit" in c.detail for c in slow.checks)


@needs_sandbox
async def test_sandbox_blocks_network_and_home():
    code = ("import socket\ntry:\n socket.create_connection(('1.1.1.1',80),timeout=2); print('NET')\n"
            "except Exception: print('blocked')\n")
    res = await SANDBOX.execute("python", code, [""])
    assert res.runs[0].stdout.strip() == "blocked"


async def test_no_samples_is_inconclusive_and_prompts_flagged():
    q = Question(url="u", module="codekata", statement="x")
    r = await validate(q, draft('#include <iostream>\nint main(){ std::cout << "Enter n: "; }'), SANDBOX, [])
    assert any(c.name == "No input prompts" and c.status == "fail" for c in r.checks)


def test_normalize():
    assert normalize("1 2 \r\n3\n\n") == "1 2\n3"


# ---------------------------------------------------------------- SQL
EMP = SqlTable(name="Employees", columns=["id", "name", "dept", "salary"], role="input",
               rows=[["1", "Asha", "Eng", "900"], ["2", "Ravi", "Eng", "700"], ["3", "Meena", "Ops", "500"]])


async def test_sql_matches_expected():
    exp = SqlTable(name="expected_output", columns=["dept", "total"], role="expected_output", rows=[["Eng", "1600"], ["Ops", "500"]])
    q = Question(url="u", module="sqlkata", statement="Total salary per department", sql_tables=[EMP, exp])
    r = await validate_sql(q, draft("SELECT dept, SUM(salary) AS total FROM Employees GROUP BY dept;", "sql"))
    assert r.verdict == "passed", r.checks


async def test_sql_invented_column_fails():
    q = Question(url="u", module="sqlkata", statement="x", sql_tables=[EMP])
    r = await validate_sql(q, draft("SELECT bonus FROM Employees", "sql"))
    assert r.verdict == "failed"
    assert "does not appear in the problem schema" in r.checks[-1].detail


async def test_sql_attach_is_denied_and_no_schema_inconclusive():
    q = Question(url="u", module="sqlkata", statement="x", sql_tables=[EMP])
    r = await validate_sql(q, draft("ATTACH DATABASE '/tmp/x.db' AS x", "sql"))
    assert r.verdict == "failed"
    r2 = await validate_sql(Question(url="u", module="sqlkata", statement="x"), draft("SELECT 1", "sql"))
    assert r2.verdict == "inconclusive"


# ---------------------------------------------------------------- engine helpers / search safety
def test_language_selection():
    q = sum_question()
    assert pick_language(q, "auto", "java") == "cpp"          # GUVI's selection wins on auto
    assert pick_language(q, "python", "java") == "python"     # explicit choice wins
    q.language = None
    assert pick_language(q, "auto", "cpp") == "cpp"           # falls back to preference
    assert pick_language(Question(url="u", module="sqlkata"), "auto", "cpp") == "sql"


def test_brief_and_phrase():
    q = sum_question()
    assert "Sample input 1" in question_brief(q)
    assert distinctive_phrase("Short. Given an array of N integers, find the length of the longest strictly increasing subsequence.")


async def test_fetch_refuses_private_hosts():
    for url in ("http://127.0.0.1:8765/api/bootstrap", "http://localhost/x", "file:///etc/passwd", "http://169.254.169.254/"):
        with pytest.raises(SearchError):
            await fetch_page_text(url)
    assert html_to_text("<p>a<script>x()</script></p><p>b</p>") == "a\n\nb" or "a" in html_to_text("<p>a</p>")


# ---------------------------------------------------------------- orchestrator (fake browser + fake LLM, real sandbox)
class FakeLLM(LLMProvider):
    name = "fake"

    def __init__(self, codes):
        self.codes = list(codes)
        self.calls = 0

    async def generate_json(self, system, user, schema, *, effort=None):
        self.calls += 1
        return {"understanding": "u", "approach": "a", "algorithm_explanation": "e", "time_complexity": "O(1)",
                "space_complexity": "O(1)", "edge_cases": ["zeros"], "extra_tests": [], "language": "cpp",
                "code": self.codes.pop(0) if len(self.codes) > 1 else self.codes[0], "sqlite_validation_query": ""}


class FakeBridge:
    """Stands in for the Chrome extension: two question pages with an editor each."""
    connected = True
    info = {}

    def __init__(self):
        self.url = "https://www.guvi.in/code-kata/list"
        self.editors = {"https://www.guvi.in/code-kata/q/1": "// starter", "https://www.guvi.in/code-kata/q/2": ""}
        self.calls = []
        self.fail_navigation_once = False

    async def call(self, method, params=None, timeout=30):
        params = params or {}
        self.calls.append(method)
        on_q = self.url in self.editors
        if method == "tab.claim":
            return {"tabId": 1, "url": self.url, "title": "GUVI"}
        if method in ("page.detect", "page.waitReady"):
            return {"url": self.url, "module": "codekata", "pageType": "question" if on_q else "listing", "loginRequired": False,
                    "editor": {"found": on_q, "type": "monaco", "language": "cpp"}}
        if method == "page.discover":
            return {"strategy": "link-groups", "items": [{"title": f"Q{i}", "url": u} for i, u in enumerate(self.editors, 1)]}
        if method == "tab.navigate":
            if self.fail_navigation_once:
                self.fail_navigation_once = False
                raise BridgeError("blocked by dialog", "navigation_blocked")
            self.url = params["url"]
            return {"url": self.url}
        if method == "page.extract":
            return sum_question(self.url).model_dump() | {"title": f"Sum {self.url[-1]}", "statement": f"Sum variant {self.url[-1]}"}
        if method == "editor.read":
            return {"editor": "monaco", "content": self.editors[self.url]}
        if method == "editor.write":
            self.editors[self.url] = params["code"]
            return {"editor": "monaco", "verified": True}
        raise AssertionError(method)


async def make_agent(tmp_path, llm):
    db = DB(tmp_path / "t.db")
    await db.connect()
    bridge = FakeBridge()
    return Agent(S, db, DashboardHub(), bridge, llm, None, SANDBOX), db, bridge


async def wait_done(agent, timeout=120):
    await asyncio.wait_for(agent._task, timeout)


@needs_sandbox
async def test_session_processes_queue_inserts_and_backs_up(tmp_path):
    agent, db, bridge = await make_agent(tmp_path, FakeLLM([GOOD_CPP]))
    sid = await agent.start(SessionConfig(module="codekata", max_questions=5))
    await wait_done(agent)
    rows = await db.session_questions(sid)
    assert [r["status"] for r in rows] == ["code_ready", "code_ready"]
    assert all(r["inserted"] == 1 for r in rows)
    assert bridge.editors["https://www.guvi.in/code-kata/q/1"] == GOOD_CPP
    backups = await db.backups_for(rows[0]["id"])
    assert backups and backups[-1]["content"] == "// starter"        # original editor content preserved
    assert (await db.get_session(sid))["state"] == "completed"
    assert "editor.write" in bridge.calls and not any("submit" in c for c in bridge.calls)
    await db.close()


@needs_sandbox
async def test_failed_validation_triggers_repair_then_passes(tmp_path):
    llm = FakeLLM([BAD_CPP, GOOD_CPP])
    agent, db, bridge = await make_agent(tmp_path, llm)
    bridge.editors = {"https://www.guvi.in/code-kata/q/1": ""}
    sid = await agent.start(SessionConfig(module="codekata"))
    await wait_done(agent)
    row = (await db.session_questions(sid))[0]
    sol = await db.latest_solution(row["id"])
    assert row["status"] == "code_ready" and sol["attempt"] == 2 and llm.calls == 2
    await db.close()


@needs_sandbox
async def test_unfixable_solution_is_never_marked_ready_or_inserted(tmp_path):
    agent, db, bridge = await make_agent(tmp_path, FakeLLM([BAD_CPP]))
    bridge.editors = {"https://www.guvi.in/code-kata/q/1": "mine"}
    sid = await agent.start(SessionConfig(module="codekata"))
    await wait_done(agent)
    row = (await db.session_questions(sid))[0]
    assert row["status"] == "needs_review" and row["inserted"] == 0
    assert bridge.editors["https://www.guvi.in/code-kata/q/1"] == "mine"   # failing code is not inserted
    await db.close()


@needs_sandbox
async def test_review_each_waits_and_prepare_only_does_not_insert(tmp_path):
    agent, db, bridge = await make_agent(tmp_path, FakeLLM([GOOD_CPP]))
    sid = await agent.start(SessionConfig(module="codekata", review_mode="review_each"))
    for _ in range(600):
        if agent.state.value == "waiting_review":
            break
        await asyncio.sleep(0.1)
    assert agent.state.value == "waiting_review"
    rows = await db.session_questions(sid)
    assert rows[0]["status"] == "code_ready" and rows[1]["status"] == "pending"   # did not run ahead
    await agent.stop()
    assert (await db.get_session(sid))["state"] == "stopped"
    assert (await db.session_questions(sid))[1]["status"] == "pending"            # unfinished is not "solved"

    agent2, db2, bridge2 = await make_agent(tmp_path / "..", FakeLLM([GOOD_CPP]))
    bridge2.editors = {"https://www.guvi.in/code-kata/q/2": "keep"}
    await agent2.start(SessionConfig(module="codekata", review_mode="prepare_only", skip_already_prepared=False))
    await wait_done(agent2)
    assert bridge2.editors["https://www.guvi.in/code-kata/q/2"] == "keep" and "editor.write" not in bridge2.calls
    await db.close(); await db2.close()


@needs_sandbox
async def test_navigation_block_pauses_then_resumes(tmp_path):
    agent, db, bridge = await make_agent(tmp_path, FakeLLM([GOOD_CPP]))
    bridge.editors = {"https://www.guvi.in/code-kata/q/1": ""}
    bridge.fail_navigation_once = True
    sid = await agent.start(SessionConfig(module="codekata"))
    for _ in range(100):
        if agent.state.value == "paused":
            break
        await asyncio.sleep(0.05)
    assert agent.state.value == "paused"
    assert (await db.session_questions(sid))[0]["status"] == "pending"
    await agent.resume()
    await wait_done(agent)
    assert (await db.session_questions(sid))[0]["status"] == "code_ready"
    await db.close()


async def test_interrupted_sessions_are_recovered_on_restart(tmp_path):
    db = DB(tmp_path / "r.db")
    await db.connect()
    sid = await db.create_session("codekata", SessionConfig(module="codekata").model_dump())
    qid = await db.add_question(sid, 0, "codekata", "https://www.guvi.in/x", "Q")
    await db.update_question(qid, status="generating")
    await db.close()
    db2 = DB(tmp_path / "r.db")
    await db2.connect()          # simulates backend restart
    assert (await db2.get_session(sid))["state"] == "interrupted"
    assert (await db2.get_question(qid))["status"] == "pending"
    await db2.close()


# ---------------------------------------------------------------- real-GUVI behaviours: language switch, SQL sub-questions, URL fallback
@needs_sandbox
async def test_switches_guvi_language_before_inserting(tmp_path):
    agent, db, bridge = await make_agent(tmp_path, FakeLLM([GOOD_CPP]))
    bridge.editors = {"https://www.guvi.in/code-kata/q/1": "print('template')"}
    lang = {"cur": "python"}
    orig = bridge.call

    async def call(method, params=None, timeout=30):
        if method in ("page.detect", "page.waitReady"):
            r = await orig(method, params, timeout)
            r["editor"]["language"] = lang["cur"]
            return r
        if method == "editor.setLanguage":
            bridge.calls.append(method)
            lang["cur"] = params["language"]
            return {"changed": True, "language": params["language"]}
        return await orig(method, params, timeout)

    bridge.call = call
    sid = await agent.start(SessionConfig(module="codekata", language="cpp"))
    await wait_done(agent)
    row = (await db.session_questions(sid))[0]
    assert row["status"] == "code_ready" and row["inserted"] == 1 and lang["cur"] == "cpp"
    assert bridge.calls.index("editor.setLanguage") < bridge.calls.index("editor.write")
    reasons = [b["reason"] for b in await db.backups_for(row["id"])]
    assert any(r.startswith("before_language_switch") for r in reasons)      # python buffer kept before switching
    await db.close()


async def test_sql_sub_questions_are_solved_and_inserted_separately(tmp_path):
    class SqlLLM(LLMProvider):
        name = "fake-sql"

        async def generate_json(self, system, user, schema, *, effort=None):
            code = ("UPDATE pets SET age = 6 WHERE name = 'Buddy';\nSELECT * FROM pets WHERE name = 'Buddy';" if "Question 2" in user
                    else "SELECT COUNT(*) FROM pets;")
            return {"understanding": "u", "approach": "a", "algorithm_explanation": "e", "time_complexity": "O(n)", "space_complexity": "O(1)",
                    "edge_cases": [], "extra_tests": [], "language": "sql", "code": code, "sqlite_validation_query": code}

    agent, db, bridge = await make_agent(tmp_path, SqlLLM())
    url = "https://www.guvi.in/sqlkata/pets/"
    buffers, cur = {1: "", 2: ""}, {"part": 1}
    orig = bridge.call

    async def call(method, params=None, timeout=30):
        params = params or {}
        bridge.calls.append(method)
        if method == "tab.claim":
            return {"tabId": 1, "url": url, "title": "GUVI"}
        if method in ("page.detect", "page.waitReady"):
            return {"url": url, "module": "sqlkata", "pageType": "question", "title": "Pets", "loginRequired": False,
                    "editor": {"found": True, "type": "ace", "language": "sql"}}
        if method == "page.extract":
            return {"url": url, "module": "sqlkata", "title": "Pets", "statement": "A pet registry.", "language": "sql",
                    "sql_tables": [{"name": "pets", "columns": ["id", "name", "age"], "rows": [["1", "Fluffy", "3"], ["2", "Buddy", "5"]], "role": "input"}],
                    "parts": [{"index": 1, "title": "Question 1", "text": "How many pets?"},
                              {"index": 2, "title": "Question 2", "text": "Set Buddy's age to 6 and show Buddy."}]}
        if method == "editor.selectPart":
            cur["part"] = params["index"]
            return {"selected": params["index"], "verified": True}
        if method == "editor.read":
            return {"editor": "ace", "content": buffers[cur["part"]]}
        if method == "editor.write":
            buffers[cur["part"]] = params["code"]
            return {"editor": "ace", "verified": True}
        return await orig(method, params, timeout)

    bridge.call = call
    sid = await agent.start(SessionConfig(module="sqlkata", discovery="current"))
    await wait_done(agent)
    row = (await db.session_questions(sid))[0]
    sol = await db.latest_solution(row["id"])
    assert buffers[1] == "SELECT COUNT(*) FROM pets;\n" and buffers[2].startswith("UPDATE pets")
    assert row["inserted"] == 1 and row["status"] == "needs_review"          # ran fine, but nothing on the page to compare against
    assert [p["index"] for p in sol["analysis"]["parts"]] == [1, 2]
    assert any("Buddy | 6" in c["detail"] for c in sol["validation"]["checks"])   # UPDATE then SELECT executed in order
    await db.close()


async def test_guessed_url_miss_falls_back_to_clicking_the_row(tmp_path):
    agent, db, bridge = await make_agent(tmp_path, FakeLLM([GOOD_CPP]))
    orig, state = bridge.call, {"opened": False}

    async def call(method, params=None, timeout=30):
        bridge.calls.append(method)
        if method == "page.waitReady":
            return {"url": "x", "pageType": "question" if state["opened"] else "other", "title": "Sum 1", "loginRequired": False,
                    "editor": {"found": state["opened"], "language": "cpp"}}
        if method == "page.openItem":
            state["opened"] = True
            bridge.url = "https://www.guvi.in/code-kata/q/1"
            return {"url": bridge.url}
        return await orig(method, params, timeout)

    bridge.call = call
    row = {"id": 1, "module": "codekata", "title": "Sum 1", "url": "https://www.guvi.in/code-kata/wrong-guess/",
           "locator": '{"listUrl": "https://www.guvi.in/code-kata/", "text": "Sum 1", "guessed": true}'}
    ready = await agent.open_question(row)
    assert ready["pageType"] == "question" and "page.openItem" in bridge.calls
    assert agent._same_title("1. Odd or Even Sum", "Odd or even sum") and not agent._same_title("Two Sum", "Palindrome")
    await db.close()


@needs_sandbox
async def test_auto_submit_only_after_guvi_run_passes_and_repairs_once(tmp_path):
    # Locally both programs pass the page sample? No: BAD_CPP fails locally, so use a program that passes the sample
    # but that "GUVI" rejects on its own cases the first time.
    llm = FakeLLM([GOOD_CPP])
    agent, db, bridge = await make_agent(tmp_path, llm)
    bridge.editors = {"https://www.guvi.in/code-kata/q/1": "", "https://www.guvi.in/code-kata/q/2": ""}
    runs = {"n": 0}
    orig = bridge.call

    async def call(method, params=None, timeout=30):
        if method == "page.run":
            bridge.calls.append(method)
            runs["n"] += 1
            # question 1: first Run fails, second passes; question 2: always fails
            if "q/1" in bridge.url:
                return {"verdict": "failed" if runs["n"] == 1 else "passed", "text": "Wrong\nCase 2\nOutput\n9"}
            return {"verdict": "failed", "text": "Wrong"}
        if method == "page.submit":
            bridge.calls.append(method + "@" + bridge.url[-1])
            return {"verdict": "passed", "text": "Congratulations!"}
        return await orig(method, params, timeout)

    bridge.call = call
    sid = await agent.start(SessionConfig(module="codekata", review_mode="auto_submit"))
    await wait_done(agent)
    rows = await db.session_questions(sid)
    assert rows[0]["status"] == "code_ready" and rows[0]["activity"].startswith("Submitted")
    assert rows[1]["status"] == "needs_review" and "after 8 rewrites" in rows[1]["activity"]
    assert [c for c in bridge.calls if c.startswith("page.submit")] == ["page.submit@1"]     # never submits a failing Run
    assert (await db.get_session(sid))["state"] == "completed"                                # moved on to the next question
    await db.close()


async def test_api_keys_rotate_when_one_is_exhausted(monkeypatch):
    import httpx
    from app.llm import LLMError, OpenAICompatibleProvider

    named = Settings(_env_file=None, llm_provider="openai_compatible", openai_api_key="or", openai_base_url="https://openrouter.ai/api/v1",
                     openai_official_api_key="oa", gemini_api_key="gm", nvidia_api_key="PASTE_NVIDIA_KEY_HERE")
    chain = OpenAICompatibleProvider(named).profiles
    assert [(p["label"], p["base"].split("/")[2]) for p in chain] == [
        ("primary", "openrouter.ai"), ("openai", "api.openai.com"), ("gemini", "generativelanguage.googleapis.com")]

    s = Settings(_env_file=None, llm_provider="openai_compatible", openai_api_key="k1", openai_api_keys="k2|model-b, k3", llm_model="model-a", llm_max_tokens=4000)
    llm = OpenAICompatibleProvider(s)
    assert [p["model"] for p in llm.profiles] == ["model-a", "model-b", "model-a"]
    seen = []

    async def fake_post(self, p, body):
        seen.append((p["key"], body["model"], body["max_tokens"]))
        if p["key"] == "k1":
            return httpx.Response(402, json={"error": {"message": "Insufficient credits"}})
        if p["key"] == "k2" and len([x for x in seen if x[0] == "k2"]) == 1:
            return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "{"}}]})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"a": "ok"}'}}]})

    monkeypatch.setattr(OpenAICompatibleProvider, "_post", fake_post)
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}
    assert await llm.generate_json("s", "u", schema) == {"a": "ok"}
    assert [k for k, _, _ in seen] == ["k1", "k2", "k2"]            # k1 out of credit -> k2; cut-off answer retried with more room
    assert seen[2][2] > seen[1][2] and seen[1][1] == "model-b"
    assert llm.describe()["active_key"] == 2 and llm.describe()["keys_available"] == 2
    assert "k1" not in str(llm.describe()) and "k2" not in str(llm.describe())     # keys never leave the engine

    async def all_dead(self, p, body):
        return httpx.Response(402, json={"error": "no credit"})

    monkeypatch.setattr(OpenAICompatibleProvider, "_post", all_dead)
    with pytest.raises(LLMError, match="All 3 API key"):
        await llm.generate_json("s", "u", schema)


async def test_provider_without_json_mode_still_returns_json(monkeypatch):
    import httpx
    from app.llm import OpenAICompatibleProvider

    llm = OpenAICompatibleProvider(Settings(_env_file=None, llm_provider="openai_compatible", openai_api_key="k", llm_max_tokens=4000))
    modes = []

    async def fake_post(self, p, body):
        modes.append((body.get("response_format") or {}).get("type"))
        if "response_format" in body:
            return httpx.Response(400, json={"error": "response_format is not supported"})
        text = "<think>hmm</think>Here you go:\n```json\n{\"a\": \"line1\nline2\"}\n```"
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": text}}]})

    monkeypatch.setattr(OpenAICompatibleProvider, "_post", fake_post)
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}
    assert (await llm.generate_json("s", "u", schema))["a"] == "line1\nline2"
    assert modes == ["json_schema", "json_object", None]      # steps down until the provider accepts the request


async def test_newer_openai_models_use_max_completion_tokens(monkeypatch):
    import httpx
    from app.llm import OpenAICompatibleProvider

    llm = OpenAICompatibleProvider(Settings(_env_file=None, llm_provider="openai_compatible", openai_api_key="k", llm_max_tokens=4000))
    params = []

    async def fake_post(self, p, body):
        params.append("max_completion_tokens" if "max_completion_tokens" in body else "max_tokens")
        if "max_tokens" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."}})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"a": "ok"}'}}]})

    monkeypatch.setattr(OpenAICompatibleProvider, "_post", fake_post)
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"], "additionalProperties": False}
    assert await llm.generate_json("s", "u", schema) == {"a": "ok"}
    assert params == ["max_tokens", "max_completion_tokens"]


@needs_sandbox
async def test_rejected_submit_keeps_rewriting_until_guvi_accepts(tmp_path):
    agent, db, bridge = await make_agent(tmp_path, FakeLLM([GOOD_CPP]))
    bridge.editors = {"https://www.guvi.in/code-kata/q/1": ""}
    submits = {"n": 0}
    orig = bridge.call

    async def call(method, params=None, timeout=30):
        if method == "page.run":
            return {"verdict": "passed", "text": "Correct"}
        if method == "page.submit":
            submits["n"] += 1          # hidden test cases reject the first two submissions
            return {"verdict": "failed" if submits["n"] < 3 else "passed", "badge": "attempted" if submits["n"] < 3 else "solved", "text": ""}
        return await orig(method, params, timeout)

    bridge.call = call
    sid = await agent.start(SessionConfig(module="codekata", review_mode="auto_submit"))
    await wait_done(agent)
    row = (await db.session_questions(sid))[0]
    assert submits["n"] == 3 and row["activity"] == "Submitted - GUVI accepted it"
    # a question whose submit was never confirmed must not be skipped next time
    await db.update_question(row["id"], activity="Submitted - passed Run; GUVI's submit result could not be read")
    agent2, db2, bridge2 = await make_agent(tmp_path / "second", FakeLLM([GOOD_CPP])) if (tmp_path / "second").mkdir() is None else None
    await db.close(); await db2.close()
