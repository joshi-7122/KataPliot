"""HTTP surface: pairing, token auth, host guard."""
from fastapi.testclient import TestClient

from app.config import get_api_token, get_settings
from app.main import app

PORT = get_settings().port
BASE = f"http://127.0.0.1:{PORT}"


def test_security_and_status():
    with TestClient(app, base_url=BASE) as c:
        assert c.get("/api/status").status_code == 401                                   # no token
        assert c.get("/api/bootstrap", headers={"Origin": "https://evil.example"}).status_code == 403
        assert c.get("/api/bootstrap", headers={"Host": "evil.example"}).status_code == 403   # DNS rebinding
        tok = c.get("/api/bootstrap").json()["token"]
        assert tok == get_api_token()
        h = {"X-KataPilot-Token": tok}
        st = c.get("/api/status", headers=h).json()
        assert st["extension"]["connected"] is False and "sandbox" in st
        r = c.post("/api/session/start", headers=h, json={"module": "codekata"})
        assert r.status_code == 409                                                       # no extension / LLM yet
        assert c.put("/api/prefs", headers=h, json={"preferred_language": "java", "bogus": 1}).json()["preferred_language"] == "java"
        c.put("/api/prefs", headers=h, json={"preferred_language": "cpp"})


def test_websockets_require_token_and_extension_origin():
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app, base_url=BASE) as c:
        tok = get_api_token()
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/ws/dashboard?token=wrong"):
                pass
        with pytest.raises(WebSocketDisconnect):   # a web page (non-extension origin) cannot pose as the extension
            with c.websocket_connect(f"/ws/extension?token={tok}", headers={"Origin": "https://evil.example"}):
                pass
        with c.websocket_connect(f"/ws/extension?token={tok}", headers={"Origin": "chrome-extension://abc"}) as ws:
            ws.send_json({"type": "hello", "data": {"version": "1.0.0"}})
            ws.send_json({"type": "ping"})
            assert ws.receive_json() == {"type": "pong"}
            st = c.get("/api/status", headers={"X-KataPilot-Token": tok}).json()
            assert st["extension"]["connected"] is True
