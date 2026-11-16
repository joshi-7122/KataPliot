"""SQLite persistence: sessions, questions, solutions, editor backups, logs, preferences."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  module TEXT NOT NULL,
  config TEXT NOT NULL,
  state TEXT NOT NULL,
  error TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  position INTEGER NOT NULL,
  url TEXT,
  locator TEXT,
  title TEXT,
  module TEXT NOT NULL,
  difficulty TEXT,
  status TEXT NOT NULL,
  activity TEXT,
  error TEXT,
  fingerprint TEXT,
  question_json TEXT,
  inserted INTEGER NOT NULL DEFAULT 0,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_questions_session ON questions(session_id);
CREATE INDEX IF NOT EXISTS idx_questions_url ON questions(url);
CREATE TABLE IF NOT EXISTS solutions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id INTEGER NOT NULL REFERENCES questions(id),
  attempt INTEGER NOT NULL,
  language TEXT,
  code TEXT NOT NULL,
  analysis_json TEXT NOT NULL,
  references_json TEXT NOT NULL,
  validation_json TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS editor_backups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id INTEGER,
  url TEXT,
  reason TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER,
  question_id INTEGER,
  level TEXT NOT NULL,
  step TEXT,
  message TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS prefs (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

DEFAULT_PREFS: dict[str, Any] = {
    "preferred_language": "cpp",
    "review_mode": "insert_and_continue",
    "max_questions": 10,
    "use_web_search": True,
    "selector_overrides": {},   # per-module CSS selector overrides from the Inspect tool
}


class DB:
    def __init__(self, path: Path):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(SCHEMA)
        # Anything left running when the backend died is recoverable, not silently "done".
        now = time.time()
        await self.conn.execute(
            "UPDATE sessions SET state='interrupted', updated_at=? WHERE state IN ('running','paused','waiting_review','stopping')",
            (now,),
        )
        await self.conn.execute(
            "UPDATE questions SET status='pending', activity='Interrupted — will retry on resume', updated_at=? "
            "WHERE status IN ('processing','researching','generating','validating','inserting')",
            (now,),
        )
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    # ---------- helpers ----------
    async def _exec(self, sql: str, args: tuple = ()) -> int:
        assert self.conn
        cur = await self.conn.execute(sql, args)
        await self.conn.commit()
        return cur.lastrowid or 0

    async def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        assert self.conn
        async with self.conn.execute(sql, args) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def _one(self, sql: str, args: tuple = ()) -> dict | None:
        rows = await self._all(sql, args)
        return rows[0] if rows else None

    # ---------- prefs ----------
    async def get_prefs(self) -> dict[str, Any]:
        rows = await self._all("SELECT key, value FROM prefs")
        prefs = dict(DEFAULT_PREFS)
        prefs.update({r["key"]: json.loads(r["value"]) for r in rows})
        return prefs

    async def set_prefs(self, updates: dict[str, Any]) -> dict[str, Any]:
        for k, v in updates.items():
            if k in DEFAULT_PREFS:
                await self._exec("INSERT INTO prefs(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                 (k, json.dumps(v)))
        return await self.get_prefs()

    # ---------- sessions ----------
    async def create_session(self, module: str, config: dict) -> int:
        now = time.time()
        return await self._exec("INSERT INTO sessions(module,config,state,created_at,updated_at) VALUES(?,?,?,?,?)",
                                (module, json.dumps(config), "running", now, now))

    async def set_session_state(self, sid: int, state: str, error: str | None = None) -> None:
        await self._exec("UPDATE sessions SET state=?, error=?, updated_at=? WHERE id=?", (state, error, time.time(), sid))

    async def get_session(self, sid: int) -> dict | None:
        return await self._one("SELECT * FROM sessions WHERE id=?", (sid,))

    async def latest_session(self) -> dict | None:
        return await self._one("SELECT * FROM sessions ORDER BY id DESC LIMIT 1")

    async def list_sessions(self, limit: int = 50) -> list[dict]:
        return await self._all(
            "SELECT s.*, (SELECT COUNT(*) FROM questions q WHERE q.session_id=s.id) AS total, "
            "(SELECT COUNT(*) FROM questions q WHERE q.session_id=s.id AND q.status='code_ready') AS ready "
            "FROM sessions s ORDER BY id DESC LIMIT ?", (limit,))

    # ---------- questions ----------
    async def add_question(self, sid: int, position: int, module: str, url: str | None, title: str,
                           locator: dict | None = None, difficulty: str | None = None) -> int:
        return await self._exec(
            "INSERT INTO questions(session_id,position,url,locator,title,module,difficulty,status,activity,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (sid, position, url, json.dumps(locator) if locator else None, title, module, difficulty, "pending", "Queued", time.time()))

    async def update_question(self, qid: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        await self._exec(f"UPDATE questions SET {cols} WHERE id=?", (*fields.values(), qid))

    async def get_question(self, qid: int) -> dict | None:
        return await self._one("SELECT * FROM questions WHERE id=?", (qid,))

    async def session_questions(self, sid: int) -> list[dict]:
        return await self._all("SELECT * FROM questions WHERE session_id=? ORDER BY position", (sid,))

    async def history(self, limit: int = 200, module: str | None = None) -> list[dict]:
        if module:
            return await self._all("SELECT * FROM questions WHERE module=? AND status!='pending' ORDER BY updated_at DESC LIMIT ?", (module, limit))
        return await self._all("SELECT * FROM questions WHERE status!='pending' ORDER BY updated_at DESC LIMIT ?", (limit,))

    async def prepared_for_url(self, url: str) -> dict | None:
        """A previous question for this URL that already has a validated solution."""
        return await self._one("SELECT * FROM questions WHERE url=? AND status='code_ready' ORDER BY updated_at DESC LIMIT 1", (url,))

    # ---------- solutions ----------
    async def add_solution(self, qid: int, attempt: int, language: str, code: str, analysis: dict,
                           references: list, validation: dict) -> int:
        return await self._exec(
            "INSERT INTO solutions(question_id,attempt,language,code,analysis_json,references_json,validation_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (qid, attempt, language, code, json.dumps(analysis), json.dumps(references), json.dumps(validation), time.time()))

    async def latest_solution(self, qid: int) -> dict | None:
        row = await self._one("SELECT * FROM solutions WHERE question_id=? ORDER BY id DESC LIMIT 1", (qid,))
        if row:
            for k in ("analysis_json", "references_json", "validation_json"):
                row[k.removesuffix("_json")] = json.loads(row.pop(k))
        return row

    # ---------- backups ----------
    async def add_backup(self, qid: int | None, url: str | None, reason: str, content: str) -> int:
        return await self._exec("INSERT INTO editor_backups(question_id,url,reason,content,created_at) VALUES(?,?,?,?,?)",
                                (qid, url, reason, content, time.time()))

    async def backups_for(self, qid: int) -> list[dict]:
        return await self._all("SELECT * FROM editor_backups WHERE question_id=? ORDER BY id DESC", (qid,))

    # ---------- logs ----------
    async def log(self, level: str, message: str, sid: int | None = None, qid: int | None = None, step: str | None = None) -> dict:
        row = {"session_id": sid, "question_id": qid, "level": level, "step": step, "message": message, "created_at": time.time()}
        row["id"] = await self._exec("INSERT INTO logs(session_id,question_id,level,step,message,created_at) VALUES(?,?,?,?,?,?)",
                                     tuple(row.values()))
        return row

    async def logs(self, sid: int | None = None, limit: int = 300) -> list[dict]:
        if sid:
            rows = await self._all("SELECT * FROM logs WHERE session_id=? ORDER BY id DESC LIMIT ?", (sid, limit))
        else:
            rows = await self._all("SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,))
        return list(reversed(rows))

    async def stats(self) -> dict[str, int]:
        row = await self._one(
            "SELECT COUNT(*) AS total, "
            "SUM(status='code_ready') AS ready, SUM(status='needs_review') AS review, "
            "SUM(status='failed') AS failed, SUM(status='pending') AS pending, SUM(inserted) AS inserted "
            "FROM questions")
        return {k: int(v or 0) for k, v in (row or {}).items()}
