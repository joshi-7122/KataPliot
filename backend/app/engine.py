"""Solution engine: web research with equivalence judging, then structured solution generation and repair."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Awaitable, Callable

from .llm import LLMProvider, generate_with_retry
from .models import Question, Reference, SolutionDraft, ValidationReport
from .search import SearchError, SearchProvider, fetch_page_text

log = logging.getLogger("katapilot.engine")
Progress = Callable[[str], Awaitable[None]]

LANG_NAMES = {"cpp": "C++17 (GCC)", "c": "C (GCC, C11)", "java": "Java 17", "python": "Python 3",
              "sql": "SQL", "html": "HTML", "css": "CSS", "javascript": "JavaScript"}

# ------------------------------------------------------------------ helpers

def question_brief(q: Question, limit: int = 9000) -> str:
    parts = [f"Title: {q.title}", f"Module: {q.module}", f"URL: {q.url}", "", "Problem statement:", q.statement]
    if q.input_format:
        parts += ["", "Input format:", q.input_format]
    if q.output_format:
        parts += ["", "Output format:", q.output_format]
    if q.constraints:
        parts += ["", "Constraints:", q.constraints]
    for i, s in enumerate(q.samples, 1):
        parts += ["", f"Sample input {i}:", s.input, f"Sample output {i}:", s.output]
    for t in q.sql_tables:
        parts += ["", f"Table `{t.name}` ({t.role}) columns: {', '.join(t.columns)}"]
        for row in t.rows[:15]:
            parts.append("  | " + " | ".join(row))
    if q.starter_code:
        parts += ["", "Code currently in the editor (starter / buggy code):", "```", q.starter_code, "```"]
    if not q.statement.strip() and q.raw_text:
        parts += ["", "Raw page text (structured extraction was incomplete):", q.raw_text]
    return "\n".join(parts)[:limit]


def distinctive_phrase(statement: str) -> str | None:
    sentences = [s.strip() for s in re.split(r"(?<=[.?!])\s+", statement) if 40 <= len(s.strip()) <= 160]
    sentences.sort(key=lambda s: -len(set(re.findall(r"[a-zA-Z]{4,}", s))))
    return sentences[0] if sentences else None


# ------------------------------------------------------------------ research

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "equivalent": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "notes": {"type": "string"},
                },
                "required": ["index", "equivalent", "confidence", "notes"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}

JUDGE_SYSTEM = """You compare an original programming/SQL problem with candidate web pages.
A candidate is "equivalent" only if it asks for the same computation with the same input/output contract
(same constraints that matter, same output format). Similar topic is NOT equivalent. Be strict.
For each candidate give confidence 0..1 and a one-sentence note on any differences (I/O format, constraints, edge cases)."""


async def research(q: Question, search: SearchProvider, llm: LLMProvider, progress: Progress,
                   max_results: int = 5, fetch_n: int = 3) -> list[Reference]:
    queries = []
    if q.title:
        queries.append(f'"{q.title}" {"SQL" if q.module == "sqlkata" else "programming problem"}')
    phrase = distinctive_phrase(q.statement)
    if phrase:
        queries.append(f'"{phrase[:120]}"')
    if q.title:
        queries.append(f"{q.title} solution site:geeksforgeeks.org OR site:leetcode.com OR site:hackerrank.com OR site:stackoverflow.com")
    if not queries:
        return []

    hits: dict[str, dict] = {}
    for query in queries:
        await progress(f"Searching: {query[:90]}")
        try:
            for h in await search.search(query, max_results):
                if h.get("url") and "guvi.in" not in h["url"] and h["url"] not in hits:
                    hits[h["url"]] = h
        except SearchError as e:
            await progress(f"Search failed ({e}); continuing")
    if not hits:
        return []

    candidates = list(hits.values())[: max_results * 2]
    await progress(f"Reading top {min(fetch_n, len(candidates))} pages")
    texts = await asyncio.gather(*(fetch_page_text(c["url"], 6000) for c in candidates[:fetch_n]), return_exceptions=True)
    for c, t in zip(candidates, texts):
        c["body"] = t if isinstance(t, str) else ""

    await progress(f"Comparing {len(candidates)} results against the original problem")
    listing = "\n\n".join(
        f"[{i}] {c['title']}\nURL: {c['url']}\nSnippet: {c.get('snippet', '')[:400]}\nPage excerpt: {c.get('body', '')[:2500]}"
        for i, c in enumerate(candidates))
    user = f"ORIGINAL PROBLEM\n{question_brief(q, 5000)}\n\nCANDIDATES\n{listing}"
    try:
        judged = await generate_with_retry(llm, JUDGE_SYSTEM, user, JUDGE_SCHEMA, effort="low")
    except Exception as e:  # research is best-effort; solving continues without it
        await progress(f"Equivalence check failed ({e}); solving independently")
        return []

    refs = []
    for r in judged.get("results", []):
        i = r.get("index", -1)
        if 0 <= i < len(candidates):
            c = candidates[i]
            refs.append(Reference(title=c["title"], url=c["url"], snippet=(c.get("body") or c.get("snippet", ""))[:3000],
                                  equivalent=bool(r["equivalent"]), confidence=float(r["confidence"]), notes=r["notes"]))
    refs.sort(key=lambda r: (-r.equivalent, -r.confidence))
    return refs


# ------------------------------------------------------------------ generation

SOLUTION_SCHEMA = {
    "type": "object",
    "properties": {
        "understanding": {"type": "string", "description": "Restate the task, inputs, outputs and constraints in your own words."},
        "approach": {"type": "string", "description": "Chosen algorithm / SQL strategy and why."},
        "algorithm_explanation": {"type": "string", "description": "Step-by-step explanation for the student."},
        "time_complexity": {"type": "string"},
        "space_complexity": {"type": "string"},
        "edge_cases": {"type": "array", "items": {"type": "string"}},
        "extra_tests": {
            "type": "array",
            "description": "2-4 small edge-case tests (input and the exact expected output) you are certain about.",
            "items": {"type": "object", "properties": {"input": {"type": "string"}, "output": {"type": "string"}},
                      "required": ["input", "output"], "additionalProperties": False},
        },
        "language": {"type": "string"},
        "code": {"type": "string", "description": "The complete final code only. No markdown fences."},
        "sqlite_validation_query": {"type": "string", "description": "SQL only: the same query rewritten for SQLite so it can be checked locally; empty string otherwise."},
    },
    "required": ["understanding", "approach", "algorithm_explanation", "time_complexity", "space_complexity",
                 "edge_cases", "extra_tests", "language", "code", "sqlite_validation_query"],
    "additionalProperties": False,
}

BASE_SYSTEM = """You are KataPilot, an expert competitive programmer and SQL engineer helping a B.Tech student
prepare solutions for GUVI practice problems. Think through the problem fully before writing code.

Rules for the code field:
- Output ONLY the final source code. No markdown fences, no prose, no explanatory comments beyond a minimal few.
- Respect the exact input/output contract: read from standard input, print exactly what is asked, no prompts like "Enter n:",
  no extra spaces/lines. GUVI compares output strictly (trailing whitespace is usually trimmed).
- Choose an algorithm that fits the constraints; state time and space complexity honestly.
- Handle edge cases (empty input, n=0/1, negatives, overflow -> use long long / long, duplicates, large inputs -> fast I/O).
- Web references are hints, not truth. Never copy a reference whose I/O contract differs from the original problem.
- If the problem statement is ambiguous, follow the sample I/O exactly and mention the ambiguity in `understanding`.
- extra_tests must be tests whose expected output you are certain about; leave the list empty if unsure.
- Spend your words on the code. Keep every other field brief: understanding, approach and algorithm_explanation
  at most 2 sentences each, at most 3 edge_cases, at most 2 extra_tests."""

MODULE_RULES = {
    "codekata": "Write a complete program in {lang}. For C++ use modern C++17, #include <bits/stdc++.h> is fine, "
                "use ios::sync_with_stdio(false); cin.tie(nullptr); for large input. For Java the public class must be named Main "
                "unless the starter code uses another name.",
    "fixthecode": "The editor contains buggy code. Find and fix the bugs with MINIMAL changes, keeping the original structure, "
                  "names and language ({lang}). Describe each bug and its fix in `approach`. Return the full corrected program.",
    "sqlkata": "Write the SQL for the TASK only: one query, or the minimal statements required (e.g. an UPDATE/INSERT followed "
               "by the SELECT that displays the result when the task asks to change data and then show it). Use ONLY tables and "
               "columns that appear in the problem; never invent schema. The platform's SQL dialect is not stated, so write "
               "portable ANSI SQL that runs unchanged on both MySQL and SQLite; avoid dialect-specific functions unless the "
               "task cannot be solved without them. Mind NULL handling, GROUP BY correctness, ORDER BY when output order is "
               "specified, and DISTINCT. Select exactly the columns the task asks for. Also provide sqlite_validation_query: "
               "the same statements in SQLite syntax for local checking (identical text when the SQL is already portable).",
    "webkata": "Produce the {lang} requested by the challenge. If the challenge asks for a full page, return a complete, valid "
               "HTML5 document with inline CSS/JS as required. Use semantic HTML, follow exact element ids/classes/text named in the task.",
}


def _lang_for(q: Question, requested: str, preferred: str) -> str:
    if q.module == "sqlkata":
        return "sql"
    if requested and requested != "auto":
        return requested
    if q.language:
        return q.language
    if q.module == "webkata":
        return "html"
    return preferred


async def generate_solution(q: Question, refs: list[Reference], llm: LLMProvider, language: str) -> tuple[SolutionDraft, list[dict]]:
    lang_label = "portable SQL" if language == "sql" else LANG_NAMES.get(language, language)
    system = BASE_SYSTEM + "\n\n" + MODULE_RULES[q.module].format(lang=lang_label)
    user = question_brief(q)
    good_refs = [r for r in refs if r.equivalent and r.confidence >= 0.6][:2]
    if good_refs:
        user += "\n\nREFERENCES judged equivalent (verify before trusting; adapt to the exact I/O above):\n"
        for r in good_refs:
            user += f"\n--- {r.title} ({r.url}) [confidence {r.confidence:.2f}; differences: {r.notes}]\n{r.snippet[:2500]}\n"
    else:
        user += "\n\nNo equivalent reference was found online; solve independently."
    user += f"\n\nRequired language: {lang_label}."
    data = await generate_with_retry(llm, system, user, SOLUTION_SCHEMA)
    return _to_draft(data, language), data.get("extra_tests", [])


async def repair_solution(q: Question, draft: SolutionDraft, report: ValidationReport, llm: LLMProvider) -> tuple[SolutionDraft, list[dict]]:
    lang_label = "portable SQL" if draft.language == "sql" else LANG_NAMES.get(draft.language, draft.language)
    system = BASE_SYSTEM + "\n\n" + MODULE_RULES[q.module].format(lang=lang_label) + \
        "\n\nThe previous attempt was judged WRONG. Do not patch it. Re-read the problem statement and the sample I/O, work out " \
        "why the output differed (wrong algorithm, wrong input parsing, wrong output format such as spacing, case, or line " \
        "breaks), then write a NEW, complete, correct program from scratch. If earlier attempts are listed, your new " \
        "program must take a genuinely different approach from each of them."
    checks = "\n".join(f"- [{c.status}] {c.name}: {c.detail[:1500]}" for c in report.checks)
    user = (f"{question_brief(q)}\n\nPREVIOUS CODE:\n{draft.code}\n\nVALIDATION RESULTS:\n{checks}\n\n"
            f"Return a newly written, correct solution in {lang_label}.")
    data = await generate_with_retry(llm, system, user, SOLUTION_SCHEMA)
    return _to_draft(data, draft.language), data.get("extra_tests", [])


def _to_draft(data: dict, language: str) -> SolutionDraft:
    code = data["code"].strip()
    fence = re.match(r"^```[\w+-]*\n(.*?)\n```$", code, re.S)
    if fence:
        code = fence.group(1)
    return SolutionDraft(
        understanding=data["understanding"], approach=data["approach"],
        algorithm_explanation=data["algorithm_explanation"], time_complexity=data["time_complexity"],
        space_complexity=data["space_complexity"], edge_cases=data["edge_cases"], language=language,
        code=code + "\n", sqlite_validation_query=(data.get("sqlite_validation_query") or None),
    )


def pick_language(q: Question, requested: str, preferred: str) -> str:
    return _lang_for(q, requested, preferred)


def dump(obj) -> str:
    return json.dumps(obj, indent=2, default=str)
