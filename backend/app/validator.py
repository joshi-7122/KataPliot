"""Validation engine: static checks, sandboxed sample tests, SQL checks on a locked-down SQLite DB, web checks.

Verdicts: "passed" (compiled + every sample matched), "failed" (compile error / runtime error / mismatch),
"inconclusive" (nothing executable to compare against). A "passed" verdict is local evidence only: it is
never reported as "accepted" - only GUVI can accept a submission.
"""
from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from html.parser import HTMLParser

from .models import CheckResult, Question, SolutionDraft, SqlTable, ValidationReport
from .sandbox import Sandbox, SandboxUnavailable


def normalize(out: str) -> str:
    lines = [ln.rstrip() for ln in out.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def tokens(out: str) -> list[str]:
    return out.split()


def _static_checks(code: str, language: str) -> list[CheckResult]:
    checks: list[CheckResult] = []
    if not code.strip():
        return [CheckResult(name="Non-empty code", status="fail", detail="The generated code is empty.")]
    if "```" in code:
        checks.append(CheckResult(name="Formatting", status="fail", detail="Markdown fences found inside the code."))
    prompt_re = r'(cout\s*<<|printf\s*\(|print\s*\(|System\.out\.print\w*\s*\()\s*f?"(Enter|Input|Please)[^"]*'
    if re.search(prompt_re, code, re.I):
        checks.append(CheckResult(name="No input prompts", status="fail",
                                  detail="The program prints an input prompt (e.g. \"Enter n\"); GUVI judges stdout strictly."))
    if language in ("cpp", "c") and re.search(r"\bfreopen\s*\(|fopen\s*\(", code):
        checks.append(CheckResult(name="Stdin/stdout only", status="warn", detail="File I/O found; GUVI expects stdin/stdout."))
    if language == "cpp" and re.search(r"\bint\s+main\s*\(", code) is None:
        checks.append(CheckResult(name="Entry point", status="fail", detail="No int main() found."))
    if language == "java" and "static void main" not in code:
        checks.append(CheckResult(name="Entry point", status="fail", detail="No public static void main found."))
    if language == "python" and re.search(r"^\s*import\s+(os|subprocess|socket)\b", code, re.M):
        checks.append(CheckResult(name="Imports", status="warn", detail="Unusual system imports for a judge problem."))
    if not checks:
        checks.append(CheckResult(name="Static checks", status="pass", detail="No formatting or I/O red flags."))
    return checks


def _likely_runtime_error(stderr: str, exit_code: int | None) -> str:
    if exit_code in (-11, 139) or "Segmentation" in stderr:
        return "Segmentation fault (out-of-bounds access or stack overflow)."
    if exit_code in (-8, 136):
        return "Arithmetic exception (division by zero?)."
    if "Exception in thread" in stderr:
        return stderr.splitlines()[0][:300]
    if "Traceback" in stderr:
        return stderr.strip().splitlines()[-1][:300]
    return (stderr.strip()[:300] or f"exit code {exit_code}")


async def validate_program(q: Question, d: SolutionDraft, sandbox: Sandbox, extra_tests: list[dict]) -> ValidationReport:
    checks = _static_checks(d.code, d.language)
    samples = [s for s in q.samples if s.input.strip() or s.output.strip()]
    tests = [(f"Sample {i + 1}", s.input, s.output, True) for i, s in enumerate(samples)]
    tests += [(f"Edge test {i + 1}", t["input"], t["output"], False) for i, t in enumerate(extra_tests[:4])]

    try:
        res = await sandbox.execute(d.language, d.code, [t[1] if t[1].endswith("\n") else t[1] + "\n" for t in tests])
    except SandboxUnavailable as e:
        checks.append(CheckResult(name="Execution", status="skipped", detail=str(e)))
        return ValidationReport(verdict="failed" if any(c.status == "fail" for c in checks) else "inconclusive",
                                checks=checks, sandbox=sandbox.name)

    if res.error:
        checks.append(CheckResult(name="Sandbox", status="skipped", detail=res.error))
        return ValidationReport(verdict="inconclusive", checks=checks, sandbox=res.backend)
    if not res.compiled:
        checks.append(CheckResult(name="Compilation", status="fail", detail=res.compile_output or "Compilation failed."))
        return ValidationReport(verdict="failed", checks=checks, sandbox=res.backend)
    warn = re.search(r"warning:", res.compile_output)
    checks.append(CheckResult(name="Compilation", status="warn" if warn else "pass",
                              detail=res.compile_output[:1500] if warn else "Compiled cleanly."))

    for (name, stdin, expected, is_sample), run in zip(tests, res.runs):
        if run.timed_out:
            checks.append(CheckResult(name=name, status="fail" if is_sample else "warn",
                                      detail=f"Time limit exceeded ({run.time_s}s). Algorithm may be too slow or waiting for input."))
            continue
        if run.exit_code not in (0, None):
            checks.append(CheckResult(name=name, status="fail" if is_sample else "warn",
                                      detail=f"Runtime error: {_likely_runtime_error(run.stderr, run.exit_code)}"))
            continue
        if normalize(run.stdout) == normalize(expected):
            checks.append(CheckResult(name=name, status="pass", detail=f"Output matches ({run.time_s}s)."))
        elif tokens(run.stdout) == tokens(expected):
            checks.append(CheckResult(name=name, status="warn",
                                      detail="Same tokens but different whitespace/line breaks. Check output formatting."))
        else:
            detail = f"Expected:\n{expected.strip()[:600]}\nGot:\n{run.stdout.strip()[:600]}"
            # LLM-proposed edge tests can themselves be wrong, so they only warn.
            checks.append(CheckResult(name=name, status="fail" if is_sample else "warn", detail=detail))

    if not samples:
        checks.append(CheckResult(name="Sample tests", status="skipped", detail="No sample input/output found on the page."))
    failed = any(c.status == "fail" for c in checks)
    verdict = "failed" if failed else ("passed" if samples else "inconclusive")
    return ValidationReport(verdict=verdict, checks=checks, sandbox=res.backend)


# ------------------------------------------------------------------ SQL

_DENY = {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}


def _authorizer(action, arg1, arg2, dbname, source):
    if action in _DENY:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA and arg1 not in ("table_info",):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _coerce(v: str):
    v = v.strip()
    if v.upper() == "NULL" or v == "":
        return None
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    return v


def _build_db(tables: list[SqlTable]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for t in tables:
        if t.role == "expected_output":
            continue
        conn.execute(f"CREATE TABLE {_ident(t.name)} ({', '.join(_ident(c) for c in t.columns)})")
        if t.rows:
            ph = ", ".join("?" * len(t.columns))
            conn.executemany(f"INSERT INTO {_ident(t.name)} VALUES ({ph})",
                             [[_coerce(v) for v in (r + [""] * len(t.columns))[: len(t.columns)]] for r in t.rows])
    conn.commit()
    conn.set_authorizer(_authorizer)
    deadline = time.monotonic() + 3.0
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    return conn


def _fmt(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return str(v)


def split_sql(script: str) -> list[str]:
    """Split a script into statements on ';' outside quotes and comments."""
    out, cur, quote, i = [], [], None, 0
    while i < len(script):
        ch = script[i]
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"', "`"):
            quote = ch
            cur.append(ch)
        elif script.startswith("--", i):
            while i < len(script) and script[i] != "\n":
                i += 1
            continue
        elif ch == ";":
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur).strip())
    return [s for s in out if s]


def _run_sql(tables: list[SqlTable], query: str) -> tuple[list[str], list[list[str]]]:
    """Run every statement on a throwaway in-memory DB; return the rows of the last statement that produced any."""
    conn = _build_db(tables)
    try:
        cols: list[str] = []
        rows: list[list[str]] = []
        for stmt in split_sql(query):
            cur = conn.execute(stmt)
            if cur.description:
                cols = [c[0] for c in cur.description]
                rows = [[_fmt(v) for v in row] for row in cur.fetchmany(5000)]
        return cols, rows
    finally:
        conn.close()


async def validate_sql(q: Question, d: SolutionDraft) -> ValidationReport:
    checks: list[CheckResult] = []
    code = d.code.strip()
    if not code:
        return ValidationReport(verdict="failed", checks=[CheckResult(name="Non-empty query", status="fail", detail="Empty query.")])
    if re.search(r"\b(DROP|DELETE|TRUNCATE|ALTER|UPDATE|INSERT)\b", code, re.I) and not re.search(
            r"\b(update|delete|insert|alter|drop)\b", q.statement, re.I):
        checks.append(CheckResult(name="Read-only", status="warn", detail="Query modifies data but the question does not ask for it."))

    known_cols = {c.lower() for t in q.sql_tables for c in t.columns} | {t.name.lower() for t in q.sql_tables}
    input_tables = [t for t in q.sql_tables if t.role != "expected_output"]
    expected = next((t for t in q.sql_tables if t.role == "expected_output"), None)
    if not input_tables:
        checks.append(CheckResult(name="Schema", status="skipped",
                                  detail="No table schema could be extracted from the page; query was not executed."))
        return ValidationReport(verdict="inconclusive", checks=checks, sandbox="sqlite")

    query = d.sqlite_validation_query or code
    try:
        cols, rows = await asyncio.wait_for(asyncio.to_thread(_run_sql, input_tables, query), 5)
    except sqlite3.DatabaseError as e:
        msg = str(e)
        mysql_only = re.search(r"near \"(LIMIT|INTERVAL|DATE_FORMAT|IFNULL|CONCAT)|no such function", msg, re.I)
        status = "warn" if mysql_only and not d.sqlite_validation_query else "fail"
        missing = re.search(r"no such (column|table): (\S+)", msg)
        if missing and missing.group(2).split(".")[-1].lower() not in known_cols:
            msg += " — the query references a column/table that does not appear in the problem schema."
            status = "fail"
        checks.append(CheckResult(name="Execute on sample data (SQLite)", status=status, detail=msg))
        return ValidationReport(verdict="failed" if status == "fail" else "inconclusive", checks=checks, sandbox="sqlite")
    except asyncio.TimeoutError:
        checks.append(CheckResult(name="Execute on sample data (SQLite)", status="fail", detail="Query took too long."))
        return ValidationReport(verdict="failed", checks=checks, sandbox="sqlite")

    preview = "\n".join(" | ".join(r) for r in rows[:6])
    checks.append(CheckResult(name="Execute on sample data (SQLite)", status="pass",
                              detail=f"Returned {len(rows)} row(s) with columns: {', '.join(cols) or '(none)'}"
                                     + (f"\n{preview}" if preview else "")))
    if expected and expected.rows:
        exp_rows = [[_fmt(_coerce(v)) for v in r] for r in expected.rows]
        ordered = bool(re.search(r"\border\s+by\b", q.statement + " " + code, re.I))
        same = rows == exp_rows if ordered else sorted(map(tuple, rows)) == sorted(map(tuple, exp_rows))
        if same:
            checks.append(CheckResult(name="Matches expected output", status="pass", detail="Result rows match the sample output table."))
        else:
            checks.append(CheckResult(name="Matches expected output", status="fail",
                                      detail=f"Expected rows {exp_rows[:8]} but got {rows[:8]}"))
        if [c.lower() for c in cols] != [c.lower() for c in expected.columns]:
            checks.append(CheckResult(name="Column names", status="warn",
                                      detail=f"Expected columns {expected.columns}, got {cols}. Check aliases."))
    else:
        checks.append(CheckResult(name="Matches expected output", status="skipped", detail="No expected output table on the page."))
    failed = any(c.status == "fail" for c in checks)
    has_expected = bool(expected and expected.rows)
    return ValidationReport(verdict="failed" if failed else ("passed" if has_expected else "inconclusive"),
                            checks=checks, sandbox="sqlite")


# ------------------------------------------------------------------ Web

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr", "!doctype"}


class _TagChecker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.scripts: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)
        self._in_script = tag == "script" and not any(k == "src" for k, _ in attrs)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                implicit = self.stack.pop()
                if implicit not in ("p", "li", "td", "th", "tr", "option"):
                    self.errors.append(f"<{implicit}> not closed before </{tag}>")
            self.stack.pop()
        else:
            self.errors.append(f"Stray </{tag}>")
        self._in_script = False

    def handle_data(self, data):
        if self._in_script and data.strip():
            self.scripts.append(data)


async def validate_web(q: Question, d: SolutionDraft, sandbox: Sandbox) -> ValidationReport:
    checks: list[CheckResult] = []
    code = d.code
    js_parts: list[str] = []
    if d.language in ("html",) or "<" in code[:200]:
        p = _TagChecker()
        p.feed(code)
        leftover = [t for t in p.stack if t not in ("p", "li", "td", "th", "tr", "option", "html", "body", "head")]
        if p.errors or leftover:
            checks.append(CheckResult(name="HTML structure", status="warn",
                                      detail="; ".join(p.errors[:5] + [f"<{t}> never closed" for t in leftover[:5]])))
        else:
            checks.append(CheckResult(name="HTML structure", status="pass", detail="Tags are balanced."))
        js_parts = p.scripts
    elif d.language == "javascript":
        js_parts = [code]
    if d.language == "css" or "<style" in code:
        css = code if d.language == "css" else " ".join(re.findall(r"(?is)<style[^>]*>(.*?)</style>", code))
        ok = css.count("{") == css.count("}")
        checks.append(CheckResult(name="CSS braces", status="pass" if ok else "fail",
                                  detail="Balanced." if ok else "Unbalanced { } in CSS."))
    for i, js in enumerate(js_parts):
        try:
            r = await sandbox.syntax_check_js(js)
            checks.append(CheckResult(name=f"JavaScript syntax #{i + 1}", status="pass" if r.compiled else "fail",
                                      detail="node --check passed." if r.compiled else r.compile_output[:1200]))
        except SandboxUnavailable as e:
            checks.append(CheckResult(name=f"JavaScript syntax #{i + 1}", status="skipped", detail=str(e)))
    # Required identifiers mentioned in the task (ids / classes in quotes or after #/.)
    wanted = set(re.findall(r'(?:id|class)\s*(?:=|of|named|as)?\s*["\']([\w-]+)["\']', q.statement, re.I))
    missing = [w for w in wanted if w not in code]
    if wanted:
        checks.append(CheckResult(name="Required ids/classes", status="fail" if missing else "pass",
                                  detail=f"Missing: {', '.join(missing)}" if missing else f"All present: {', '.join(sorted(wanted))}"))
    failed = any(c.status == "fail" for c in checks)
    return ValidationReport(verdict="failed" if failed else "inconclusive", checks=checks, sandbox=sandbox.name)


async def validate(q: Question, d: SolutionDraft, sandbox: Sandbox, extra_tests: list[dict]) -> ValidationReport:
    if d.language == "sql":
        return await validate_sql(q, d)
    if q.module == "webkata" or d.language in ("html", "css"):
        return await validate_web(q, d, sandbox)
    return await validate_program(q, d, sandbox, extra_tests)
