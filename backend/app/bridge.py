"""Two WebSocket hubs:

* DashboardHub - fan-out of live agent events to every open dashboard.
* ExtensionBridge - JSON-RPC over WebSocket to the Chrome extension's service worker, which executes
  browser actions inside the user's own (already logged-in) GUVI tab.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("katapilot.bridge")


class DashboardHub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def add(self, ws: WebSocket) -> None:
        self.clients.add(ws)

    def remove(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def emit(self, type_: str, **data: Any) -> None:
        msg = json.dumps({"type": type_, "data": data, "ts": time.time()}, default=str)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove(ws)


class BridgeError(RuntimeError):
    """Raised when the extension is missing, times out, or reports an error."""

    def __init__(self, message: str, code: str = "bridge_error"):
        super().__init__(message)
        self.code = code


class ExtensionBridge:
    def __init__(self, hub: DashboardHub) -> None:
        self.hub = hub
        self.ws: WebSocket | None = None
        self.info: dict[str, Any] = {}
        self._pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self.last_seen = 0.0

    @property
    def connected(self) -> bool:
        return self.ws is not None

    async def attach(self, ws: WebSocket, info: dict[str, Any]) -> None:
        if self.ws is not None:
            try:
                await self.ws.close(code=4000, reason="replaced by a newer extension connection")
            except Exception:
                pass
        self.ws, self.info, self.last_seen = ws, info, time.time()
        await self.hub.emit("extension", connected=True, info=info)

    async def detach(self, ws: WebSocket) -> None:
        if self.ws is ws:
            self.ws = None
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(BridgeError("Chrome extension disconnected", "extension_disconnected"))
            self._pending.clear()
            await self.hub.emit("extension", connected=False, info={})

    async def handle_message(self, msg: dict[str, Any]) -> None:
        self.last_seen = time.time()
        if msg.get("type") == "response":
            fut = self._pending.pop(int(msg.get("id", -1)), None)
            if fut and not fut.done():
                if msg.get("ok"):
                    fut.set_result(msg.get("result"))
                else:
                    err = msg.get("error") or {}
                    fut.set_exception(BridgeError(err.get("message", "Unknown extension error"), err.get("code", "extension_error")))
        elif msg.get("type") == "event":
            # Unsolicited extension events (tab changed, GUVI page detected, ...) go straight to dashboards.
            await self.hub.emit("browser", **(msg.get("data") or {}))

    async def call(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
        if not self.ws:
            raise BridgeError("Chrome extension is not connected. Open Chrome with the KataPilot extension installed.",
                              "extension_disconnected")
        rid = next(self._ids)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self.ws.send_text(json.dumps({"type": "request", "id": rid, "method": method, "params": params or {}}))
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise BridgeError(f"Browser action '{method}' timed out after {timeout:.0f}s", "timeout") from None
        finally:
            self._pending.pop(rid, None)
