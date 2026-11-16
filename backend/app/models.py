"""Shared data structures."""
from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

Module = Literal["codekata", "sqlkata", "webkata", "fixthecode"]
Language = Literal["cpp", "c", "java", "python", "sql", "html", "css", "javascript"]


class QStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    researching = "researching"
    generating = "generating"
    validating = "validating"
    inserting = "inserting"
    code_ready = "code_ready"
    needs_review = "needs_review"
    failed = "failed"
    skipped = "skipped"


TERMINAL = {QStatus.code_ready, QStatus.needs_review, QStatus.failed, QStatus.skipped}


class SessionState(str, Enum):
    idle = "idle"
    running = "running"
    paused = "paused"
    waiting_review = "waiting_review"
    stopping = "stopping"
    stopped = "stopped"
    completed = "completed"
    interrupted = "interrupted"
    failed = "failed"


class SampleCase(BaseModel):
    input: str = ""
    output: str = ""


class SqlTable(BaseModel):
    name: str
    columns: list[str]
    rows: list[list[str]] = []
    role: Literal["input", "expected_output", "unknown"] = "unknown"


class QuestionPart(BaseModel):
    """One sub-question of a multi-part problem (GUVI SQLKata), each with its own editor buffer."""
    index: int
    title: str = ""
    text: str = ""


class Question(BaseModel):
    """Structured question object produced from the GUVI page."""
    url: str
    module: Module
    title: str = ""
    statement: str = ""
    input_format: str = ""
    output_format: str = ""
    constraints: str = ""
    samples: list[SampleCase] = []
    language: str | None = None          # language selected in the GUVI editor, if detected
    difficulty: str | None = None
    starter_code: str | None = None      # existing editor content (FixTheCode / templates)
    sql_tables: list[SqlTable] = []
    parts: list[QuestionPart] = []
    raw_text: str = ""
    extraction_warnings: list[str] = []

    @property
    def fingerprint(self) -> str:
        basis = (self.title.strip().lower() + "\n" + self.statement.strip().lower()[:2000]).encode()
        return hashlib.sha256(basis).hexdigest()[:16]


class Reference(BaseModel):
    title: str
    url: str
    snippet: str = ""
    equivalent: bool = False
    confidence: float = 0.0
    notes: str = ""


class SolutionDraft(BaseModel):
    understanding: str
    approach: str
    algorithm_explanation: str
    time_complexity: str
    space_complexity: str
    edge_cases: list[str]
    language: str
    code: str
    sqlite_validation_query: str | None = None  # SQL only: SQLite-compatible variant for local checks


class CheckResult(BaseModel):
    name: str
    status: Literal["pass", "fail", "warn", "skipped"]
    detail: str = ""


class ValidationReport(BaseModel):
    verdict: Literal["passed", "failed", "inconclusive"]
    checks: list[CheckResult] = []
    sandbox: str = "none"

    @property
    def failure_summary(self) -> str:
        return "\n".join(f"- {c.name}: {c.detail}" for c in self.checks if c.status == "fail")


class SessionConfig(BaseModel):
    module: Module
    language: str = "auto"            # "auto" = use the language selected in GUVI, else preferred
    review_mode: Literal["prepare_only", "insert_and_continue", "review_each", "auto_submit"] = "insert_and_continue"
    discovery: Literal["listing", "current", "urls"] = "listing"
    urls: list[str] = []
    max_questions: int = Field(default=10, ge=1, le=200)
    skip_already_prepared: bool = True
    skip_solved_on_guvi: bool = True   # skip list rows GUVI already marks as solved
    use_web_search: bool = True


class Event(BaseModel):
    type: str
    data: dict[str, Any] = {}
