"""FastAPI entrypoint: REST for controls, WebSockets for the extension bridge and live dashboard events."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .agent import Agent
from .bridge import BridgeError, DashboardHub, ExtensionBridge
from .config import get_api_token, get_settings
from .db import DB
from .llm import LLMError, make_llm
from .models import SessionConfig
from .sandbox import make_sandbox
from .search import make_search

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("katapilot")
S = get_settings()
TOKEN = get_api_token()


class State:
    db: DB
    hub: DashboardHub
    bridge: ExtensionBridge
    agent: Agent
    llm_error: str | None = None


st = State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    st.db = DB(S.db_path)
    await st.db.connect()
    st.hub = DashboardHub()
    st.bridge = ExtensionBridge(st.hub)
    try:
        llm = make_llm(S)
    except LLMError as e:
        llm, st.llm_error = None, str(e)
    st.agent = Agent(S, st.db, st.hub, st.bridge, llm, make_search(S), make_sandbox(S))
    log.info("KataPilot backend on http://%s:%s  (sandbox=%s, search=%s, llm=%s)", S.host, S.port,
             st.agent.sandbox.name, S.search_provider, S.llm_provider)
    yield
    await st.agent.stop()
    await st.db.close()


app = FastAPI(title="KataPilot AI", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=S.origins, allow_origin_regex=r"chrome-extension://[a-p]{32}",
                   allow_methods=["*"], allow_headers=["*"])

ALLOWED_HOSTS = {f"localhost:{S.port}", f"127.0.0.1:{S.port}", "localhost:5173", "127.0.0.1:5173"}


@app.middleware("http")
async def host_guard(request: Request, call_next):
    # Blocks DNS-rebinding: only accept requests addressed to localhost.
    if request.headers.get("host") not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "invalid host"}, status_code=403)
    return await call_next(request)


def _origin_ok(origin: str | None) -> bool:
    return origin is None or origin in S.origins or origin.startswith("chrome-extension://")


def require_token(x_katapilot_token: str | None = Header(default=None)):
    if x_katapilot_token != TOKEN:
        raise HTTPException(401, "Missing or invalid KataPilot token")


Auth = Depends(require_token)


@app.get("/api/bootstrap")
async def bootstrap(request: Request):
    """Hands the pairing token to the dashboard (same origin) or the extension. Other websites are refused."""
    if not _origin_ok(request.headers.get("origin")):
        raise HTTPException(403, "origin not allowed")
    return {"token": TOKEN}


async def _bridge_call(method: str, params: dict | None = None, timeout: float = 30) -> Any:
    try:
        return await st.bridge.call(method, params, timeout)
    except BridgeError as e:
        raise HTTPException(409 if e.code == "extension_disconnected" else 502, str(e))


# ------------------------------------------------------------------ status & prefs
@app.get("/api/status", dependencies=[Auth])
async def status():
    a = st.agent
    return {
        "extension": {"connected": st.bridge.connected, "info": st.bridge.info},
        "agent": a.snapshot(),
        "llm": a.llm.describe() if a.llm else {"provider": S.llm_provider, "error": st.llm_error},
        "search": {"provider": S.search_provider if a.search else "none",
                   "configured": a.search is not None},
        "sandbox": {"backend": a.sandbox.name},
        "stats": await st.db.stats(),
    }


@app.get("/api/prefs", dependencies=[Auth])
async def get_prefs():
    return await st.db.get_prefs()


@app.put("/api/prefs", dependencies=[Auth])
async def put_prefs(body: dict[str, Any]):
    return await st.db.set_prefs(body)


# ------------------------------------------------------------------ session control
@app.post("/api/session/start", dependencies=[Auth])
async def start(cfg: SessionConfig):
    try:
        sid = await st.agent.start(cfg)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"session_id": sid}


@app.post("/api/session/{action}", dependencies=[Auth])
async def control(action: str):
    a = st.agent
    if action == "pause":
        await a.pause()
    elif action == "resume":
        await a.resume()
    elif action == "skip":
        await a.skip()
    elif action == "stop":
        await a.stop()
    else:
        raise HTTPException(404, "unknown action")
    return a.snapshot()


@app.post("/api/sessions/{sid}/resume", dependencies=[Auth])
async def resume_interrupted(sid: int):
    try:
        await st.agent.resume_interrupted(sid)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return st.agent.snapshot()


@app.get("/api/session/current", dependencies=[Auth])
async def current():
    sid = st.agent.session_id
    row = await st.db.get_session(sid) if sid else await st.db.latest_session()
    if not row:
        return {"session": None, "questions": [], "logs": [], "agent": st.agent.snapshot()}
    return {"session": row, "questions": await st.db.session_questions(row["id"]),
            "logs": await st.db.logs(row["id"], 200), "agent": st.agent.snapshot()}


@app.get("/api/sessions", dependencies=[Auth])
async def sessions():
    return await st.db.list_sessions()


@app.get("/api/history", dependencies=[Auth])
async def history(module: str | None = Query(default=None)):
    return await st.db.history(module=module)


@app.get("/api/questions/{qid}", dependencies=[Auth])
async def question(qid: int):
    row = await st.db.get_question(qid)
    if not row:
        raise HTTPException(404, "not found")
    row["question"] = json.loads(row.pop("question_json") or "null")
    return {"row": row, "solution": await st.db.latest_solution(qid), "backups": await st.db.backups_for(qid)}


@app.post("/api/questions/{qid}/insert", dependencies=[Auth])
async def insert(qid: int):
    try:
        return await st.agent.manual_insert(qid)
    except (RuntimeError, BridgeError) as e:
        raise HTTPException(409, str(e))
    except Exception as e:  # PauseSession etc. carry a user-facing message
        raise HTTPException(409, str(e))


@app.post("/api/backups/{bid}/restore", dependencies=[Auth])
async def restore(bid: int):
    rows = await st.db._all("SELECT * FROM editor_backups WHERE id=?", (bid,))
    if not rows:
        raise HTTPException(404, "backup not found")
    current_content = await _bridge_call("editor.read")
    await st.db.add_backup(rows[0]["question_id"], rows[0]["url"], "before_restore", (current_content or {}).get("content") or "")
    return await _bridge_call("editor.write", {"code": rows[0]["content"]})


# ------------------------------------------------------------------ browser diagnostics
@app.post("/api/browser/detect", dependencies=[Auth])
async def detect():
    await _bridge_call("tab.claim")
    return await _bridge_call("page.detect", {})


@app.post("/api/browser/inspect", dependencies=[Auth])
async def inspect():
    """Structural snapshot of the GUVI page (tags/classes/headings, no personal text) to tune the adapter."""
    await _bridge_call("tab.claim")
    return await _bridge_call("page.inspect", {}, timeout=30)


@app.post("/api/browser/extract", dependencies=[Auth])
async def extract_now(module: str = Query(default="codekata")):
    await _bridge_call("tab.claim")
    return await _bridge_call("page.extract", {"module": module})


# ------------------------------------------------------------------ websockets
@app.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket, token: str = Query(default="")):
    if token != TOKEN or not _origin_ok(ws.headers.get("origin")):
        await ws.close(code=4401)
        return
    await ws.accept()
    await st.hub.add(ws)
    await ws.send_text(json.dumps({"type": "hello", "data": {"agent": st.agent.snapshot(),
                                                             "extension": {"connected": st.bridge.connected}}}))
    try:
        while True:
            await ws.receive_text()   # dashboards only listen; keepalive pings arrive here
    except WebSocketDisconnect:
        pass
    finally:
        st.hub.remove(ws)


@app.websocket("/ws/extension")
async def ws_extension(ws: WebSocket, token: str = Query(default="")):
    origin = ws.headers.get("origin") or ""
    if token != TOKEN or not origin.startswith("chrome-extension://"):
        await ws.close(code=4401)
        return
    await ws.accept()
    try:
        hello = json.loads(await ws.receive_text())
        await st.bridge.attach(ws, {**(hello.get("data") or {}), "origin": origin})
        while True:
            msg = json.loads(await ws.receive_text())
            if msg.get("type") == "ping":
                await ws.send_text('{"type":"pong"}')
                continue
            await st.bridge.handle_message(msg)
    except (WebSocketDisconnect, json.JSONDecodeError):
        pass
    finally:
        await st.bridge.detach(ws)


# ------------------------------------------------------------------ dashboard static build
if S.dashboard_dist.exists():
    app.mount("/assets", StaticFiles(directory=S.dashboard_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        f = S.dashboard_dist / path
        if path and f.is_file() and S.dashboard_dist in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(S.dashboard_dist / "index.html")


def run():
    import uvicorn

    uvicorn.run("app.main:app", host=S.host, port=S.port, log_level="info")


if __name__ == "__main__":
    run()
