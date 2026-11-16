"""Tests every configured LLM key without printing any of them.

    .venv/bin/python check_keys.py

For each provider it lists the models your key can use, checks the model named in .env, and makes one tiny request.
"""
import asyncio
import sys

import httpx

from app.config import get_settings
from app.llm import LLMError, make_llm

HINTS = {"openai": ("gpt", "OPENAI_OFFICIAL_MODEL"), "gemini": ("flash", "GEMINI_MODEL"), "nvidia": ("nemotron", "NVIDIA_MODEL"), "primary": ("", "LLM_MODEL")}


async def check(p: dict) -> bool:
    label = p["label"]
    print(f"\n[{label}]  model: {p['model']}  ({p['base'].split('/')[2]})")
    head = {"Authorization": f"Bearer {p['key']}"}
    async with httpx.AsyncClient(timeout=40) as c:
        try:
            r = await c.get(f"{p['base']}/models", headers=head)
            ids = [m.get("id", "").removeprefix("models/") for m in r.json().get("data", [])] if r.status_code == 200 else []
        except Exception:
            ids = []
        if ids and p["model"] not in ids and p["model"].removeprefix("models/") not in ids:
            word, var = HINTS.get(label, ("", "the model"))
            close = [i for i in ids if word and word in i.lower()][:8]
            print(f"  ! model not in this key's list. Set {var} in .env to one of: {', '.join(close) or ', '.join(ids[:8])}")
        try:
            ask = {"model": p["model"], "messages": [{"role": "user", "content": "Reply with the single word OK"}]}
            r = await c.post(f"{p['base']}/chat/completions", headers=head, json=ask | {"max_tokens": 16})
            if r.status_code == 400 and "max_completion_tokens" in r.text:
                r = await c.post(f"{p['base']}/chat/completions", headers=head, json=ask | {"max_completion_tokens": 200})
        except httpx.HTTPError as e:
            print(f"  x could not connect: {type(e).__name__}")
            return False
    if r.status_code == 200:
        print("  ok - key works")
        return True
    reason = {401: "key rejected", 403: "key rejected / not permitted", 402: "no credit", 404: "model not found", 429: "rate limited / quota used up"}
    print(f"  x HTTP {r.status_code}: {reason.get(r.status_code, r.text[:160].strip())}")
    return False


async def main() -> int:
    s = get_settings()
    if s.llm_provider != "openai_compatible":
        print("LLM_PROVIDER is not openai_compatible; key rotation applies only to that provider.")
        return 1
    try:
        llm = make_llm(s)
    except LLMError as e:
        print(e)
        return 1
    print(f"{len(llm.profiles)} key(s) configured, used in this order: " + " -> ".join(p["label"] for p in llm.profiles))
    for name, key in (("openai", s.openai_official_api_key), ("nvidia", s.nvidia_api_key), ("gemini", s.gemini_api_key)):
        if not key or key.upper().startswith("PASTE"):
            print(f"  ({name}: no key pasted yet)")
    ok = [await check(p) for p in llm.profiles]
    print(f"\n{sum(ok)} of {len(ok)} key(s) working.")
    return 0 if all(ok) else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
