"""End-to-end UI harness (testing only - never used by the product).

Runs the real FastAPI app + real sandbox + real dashboard build, with two stand-ins:
  * a fake "extension" WebSocket client that behaves like a GUVI tab with three questions;
  * a scripted LLM (the second question's first attempt is deliberately wrong to exercise repair).

    PORT=8766 DATA_DIR=/private/tmp/katapilot-e2e python tests/e2e_harness.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PORT", "8766")
os.environ.setdefault("DATA_DIR", "/private/tmp/katapilot-e2e")

import uvicorn  # noqa: E402
import websockets  # noqa: E402

from app import main  # noqa: E402
from app.llm import LLMProvider  # noqa: E402

PORT = int(os.environ["PORT"])
GOOD = ('#include <bits/stdc++.h>\nusing namespace std;\n\nint main() {\n    ios::sync_with_stdio(false);\n    cin.tie(nullptr);\n'
        '    int n;\n    cin >> n;\n    long long sum = 0;\n    for (int i = 0; i < n; i++) {\n        long long x;\n        cin >> x;\n'
        '        if (x % 2 == 0) sum += x;\n    }\n    cout << sum << "\\n";\n    return 0;\n}\n')
WRONG = GOOD.replace("x % 2 == 0", "x % 2 != 0")
QUESTIONS = {
    f"https://www.guvi.in/code-kata/problem/{i}": {"title": t, "editor": "// write your code here\n"}
    for i, t in ((101, "Sum of Even Numbers"), (102, "Even Sum in a Stream"), (103, "Total of Even Elements"))
}


class ScriptedLLM(LLMProvider):
    name = "scripted-test-llm"

    def __init__(self):
        self.seen_repair = False

    def describe(self):
        return {"provider": self.name, "model": "scripted"}

    async def generate_json(self, system, user, schema, *, effort=None):
        await asyncio.sleep(1.6)
        code = GOOD
        if "Even Sum in a Stream" in user and "PREVIOUS CODE" not in user:
            code = WRONG
        return {"understanding": "Read N integers and add up the even ones.",
                "approach": "Single pass over the input, accumulating even values in a 64-bit integer.",
                "algorithm_explanation": "1. Read N.\n2. For each value, test x % 2 == 0.\n3. Add matches to a running long long sum.\n4. Print the sum.",
                "time_complexity": "O(N)", "space_complexity": "O(1)",
                "edge_cases": ["No even numbers -> prints 0", "Negative even numbers", "Sum exceeds 32-bit range"],
                "extra_tests": [{"input": "3\n1 3 5", "output": "0"}], "language": "cpp", "code": code, "sqlite_validation_query": ""}


async def fake_extension():
    state = {"url": "https://www.guvi.in/code-kata/arrays"}
    token = main.TOKEN
    async for ws in websockets.connect(f"ws://127.0.0.1:{PORT}/ws/extension?token={token}", origin="chrome-extension://e2e-harness"):
        try:
            await ws.send(json.dumps({"type": "hello", "data": {"version": "e2e-harness"}}))
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") != "request":
                    continue
                m, p = msg["method"], msg.get("params") or {}
                await asyncio.sleep(0.5)
                url = state["url"]
                on_q = url in QUESTIONS
                if m == "tab.claim":
                    r = {"tabId": 1, "url": url, "title": "GUVI | CodeKata"}
                elif m in ("page.detect", "page.waitReady"):
                    r = {"url": url, "module": "codekata", "pageType": "question" if on_q else "listing", "loginRequired": False,
                         "listingCount": len(QUESTIONS), "editor": {"found": on_q, "type": "monaco", "language": "cpp"}}
                elif m == "page.discover":
                    r = {"strategy": "link-groups", "items": [{"title": q["title"], "url": u, "difficulty": "easy"} for u, q in QUESTIONS.items()]}
                elif m == "tab.navigate":
                    state["url"] = p["url"]
                    r = {"url": p["url"]}
                elif m == "page.extract":
                    q = QUESTIONS[url]
                    r = {"url": url, "module": "codekata", "title": q["title"],
                         "statement": f"{q['title']}: you are given N followed by N integers. Print the sum of all even numbers among them.",
                         "input_format": "First line contains N. Second line contains N space separated integers.",
                         "output_format": "Print a single integer.", "constraints": "1 <= N <= 100000",
                         "samples": [{"input": "5\n1 2 3 4 5", "output": "6"}], "language": "cpp", "difficulty": "easy",
                         "starter_code": q["editor"], "sql_tables": [], "raw_text": "", "extraction_warnings": []}
                elif m == "editor.read":
                    r = {"editor": "monaco", "content": QUESTIONS[url]["editor"] if on_q else ""}
                elif m == "editor.write":
                    QUESTIONS[url]["editor"] = p["code"]
                    r = {"editor": "monaco", "verified": True}
                else:
                    await ws.send(json.dumps({"type": "response", "id": msg["id"], "ok": False, "error": {"message": f"unsupported {m}", "code": "bad_method"}}))
                    continue
                await ws.send(json.dumps({"type": "response", "id": msg["id"], "ok": True, "result": r}))
        except websockets.ConnectionClosed:
            await asyncio.sleep(1)


async def run():
    server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=PORT, log_level="warning"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.1)
    main.st.agent.llm = ScriptedLLM()
    main.st.llm_error = None
    ext = asyncio.create_task(fake_extension())
    print(f"e2e harness ready on http://127.0.0.1:{PORT}", flush=True)
    await task
    ext.cancel()


if __name__ == "__main__":
    asyncio.run(run())
