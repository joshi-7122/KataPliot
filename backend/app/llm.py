"""Pluggable LLM providers. Each provider returns a dict validated against a JSON schema.

* AnthropicProvider - official `anthropic` SDK, structured outputs via output_config.format.
* OpenAICompatibleProvider - any OpenAI-style /chat/completions endpoint (OpenAI, Groq, OpenRouter,
  Together, local Ollama ...), JSON-schema response_format.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger("katapilot.llm")


class LLMError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class LLMProvider(ABC):
    name: str

    @abstractmethod
    async def generate_json(self, system: str, user: str, schema: dict[str, Any], *, effort: str | None = None) -> dict[str, Any]:
        ...

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name}


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, s: Settings):
        import anthropic

        self._anthropic = anthropic
        # The SDK resolves ANTHROPIC_API_KEY / `ant auth login` profiles itself when no key is set in .env.
        kwargs = {"api_key": s.anthropic_api_key} if s.anthropic_api_key else {}
        self.client = anthropic.AsyncAnthropic(max_retries=4, **kwargs)
        import os
        from pathlib import Path
        self._has_credentials = bool(s.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                                     or (Path.home() / ".config" / "anthropic").exists())
        self.model = s.llm_model
        self.effort = s.llm_effort
        self.max_tokens = s.llm_max_tokens

    def describe(self) -> dict[str, Any]:
        d: dict[str, Any] = {"provider": self.name, "model": self.model, "effort": self.effort}
        if not self._has_credentials:
            d["error"] = "No Anthropic API key found. Set ANTHROPIC_API_KEY in backend/.env and restart the backend."
        return d

    async def generate_json(self, system, user, schema, *, effort=None):
        a = self._anthropic
        try:
            # Streaming avoids HTTP timeouts on long reasoning turns. Server-side fallbacks re-run the
            # request on another model if the primary model declines it.
            async with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                thinking={"type": "adaptive"},
                output_config={"effort": effort or self.effort} | ({"format": {"type": "json_schema", "schema": schema}} if schema else {}),
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                msg = await stream.get_final_message()
        except TypeError as e:   # SDK raises this when it cannot find any credential
            if "authentication" in str(e).lower():
                raise LLMError("No Anthropic API key found. Set ANTHROPIC_API_KEY in backend/.env and restart.") from e
            raise
        except a.AuthenticationError as e:
            raise LLMError("Anthropic API key is missing or invalid (set ANTHROPIC_API_KEY in backend/.env).") from e
        except a.RateLimitError as e:
            raise LLMError("Anthropic rate limit reached.", retryable=True) from e
        except a.BadRequestError as e:
            raise LLMError(f"Anthropic rejected the request: {e.message}") from e
        except a.APIStatusError as e:
            raise LLMError(f"Anthropic API error {e.status_code}: {e.message}", retryable=e.status_code >= 500) from e
        except a.APIConnectionError as e:
            raise LLMError("Could not reach the Anthropic API (network).", retryable=True) from e

        if msg.stop_reason == "refusal":
            raise LLMError("The model declined to answer this question.")
        if msg.stop_reason == "max_tokens":
            raise LLMError("The model ran out of output tokens; raise LLM_MAX_TOKENS.", retryable=False)
        text = next((b.text for b in msg.content if b.type == "text"), "")
        if schema is None:
            return {"text": text}
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError("Model returned malformed JSON.", retryable=True) from e


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-style /chat/completions with automatic rotation across several API keys."""
    name = "openai_compatible"

    def __init__(self, s: Settings):
        self.max_tokens = s.llm_max_tokens
        self.profiles: list[dict] = []
        def add(label: str, key: str | None, model: str, base: str) -> None:
            key = (key or "").strip()
            if not key or key.upper().startswith("PASTE") or any(p["key"] == key for p in self.profiles):
                return
            self.profiles.append({"label": label, "key": key, "model": model, "base": base.rstrip("/"),
                                  "blocked_until": 0.0, "why": "", "mode": 2,    # mode: 2 JSON schema, 1 JSON object, 0 prompt only
                                  "tok": "max_tokens"})

        add("primary", s.openai_api_key, s.llm_model, s.openai_base_url)
        add("openai", s.openai_official_api_key, s.openai_official_model, s.openai_official_base_url)
        add("nvidia", s.nvidia_api_key, s.nvidia_model, s.nvidia_base_url)
        add("gemini", s.gemini_api_key, s.gemini_model, s.gemini_base_url)
        for n, e in enumerate([e for e in re.split(r"[,\n]", s.openai_api_keys or "") if e.strip()], 1):
            parts = [x.strip() for x in e.strip().split("|")]
            add(f"extra{n}", parts[0], parts[1] if len(parts) > 1 and parts[1] else s.llm_model,
                parts[2] if len(parts) > 2 and parts[2] else s.openai_base_url)
        if not self.profiles:
            if "localhost" in s.openai_base_url or "127.0.0.1" in s.openai_base_url:
                self.profiles.append({"label": "local", "key": "none", "model": s.llm_model, "base": s.openai_base_url.rstrip("/"),
                                      "blocked_until": 0.0, "why": "", "mode": 2, "tok": "max_tokens"})
            else:
                raise LLMError("OPENAI_API_KEY (or OPENAI_API_KEYS) is required for the openai_compatible provider.")
        self.active = 0

    @staticmethod
    def _friendly_provider(base_url: str) -> str:
        """Derive a human-readable provider name from the API base URL."""
        host = base_url.split("//")[-1].split("/")[0].split(":")[0].lower()
        NAMES = {"openrouter.ai": "OpenRouter", "api.openai.com": "OpenAI",
                 "generativelanguage.googleapis.com": "Gemini", "integrate.api.nvidia.com": "NVIDIA",
                 "api.groq.com": "Groq", "api.together.xyz": "Together", "api.fireworks.ai": "Fireworks"}
        return NAMES.get(host, host.split(".")[0].capitalize() if "localhost" not in host and "127.0.0.1" not in host else "Local")

    @staticmethod
    def _friendly_model(model: str) -> str:
        """Strip vendor prefixes like 'openai/' from model names for display."""
        return model.split("/", 1)[-1] if "/" in model else model

    def describe(self):
        now = time.time()
        p = self.profiles[self.active]
        return {"provider": self._friendly_provider(p["base"]), "model": self._friendly_model(p["model"]),
                "full_model": p["model"], "base_url": p["base"], "keys": len(self.profiles),
                "active_key": self.active + 1, "active_label": p["label"],
                "keys_available": sum(1 for x in self.profiles if x["blocked_until"] <= now),
                "chain": [{"label": x["label"], "model": self._friendly_model(x["model"]),
                           "provider": self._friendly_provider(x["base"]),
                           "available": x["blocked_until"] <= now, "note": x["why"]}
                          for x in self.profiles]}

    def _pick(self) -> dict | None:
        now = time.time()
        for k in range(len(self.profiles)):
            idx = (self.active + k) % len(self.profiles)
            if self.profiles[idx]["blocked_until"] <= now:
                self.active = idx
                return self.profiles[idx]
        return None

    def _block(self, p: dict, seconds: float, why: str) -> None:
        p["blocked_until"], p["why"] = time.time() + seconds, why
        n = self.profiles.index(p) + 1
        log.warning("API key %d of %d (%s) set aside for %.0f min: %s", n, len(self.profiles), p["label"], seconds / 60, why)

    async def _post(self, p: dict, body: dict) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=300) as c:
                return await c.post(f"{p['base']}/chat/completions", json=body,
                                    headers={"Authorization": f"Bearer {p['key']}", "X-Title": "KataPilot"})
        except httpx.HTTPError as e:
            raise LLMError(f"Could not reach the LLM provider ({type(e).__name__}).", retryable=True) from e

    async def generate_json(self, system, user, schema, *, effort=None):
        max_tokens = min(self.max_tokens, 16000)
        grew = False
        for _ in range(len(self.profiles) * 2 + 3):
            p = self._pick()
            if p is None:
                soonest = min(self.profiles, key=lambda x: x["blocked_until"])
                mins = max(1, int((soonest["blocked_until"] - time.time()) / 60))
                raise LLMError(f"All {len(self.profiles)} API key(s) are exhausted or rejected ({soonest['why']}). "
                               f"Add another key to OPENAI_API_KEYS in backend/.env, or wait about {mins} min.")
            body = {"model": p["model"], p["tok"]: max_tokens,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if schema is None:
                pass                                   # plain-text answer (code in a fenced block)
            elif p["mode"] == 2:
                body["response_format"] = {"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}}
            else:
                if p["mode"] == 1:
                    body["response_format"] = {"type": "json_object"}
                body["messages"][0]["content"] = (system + "\n\nReply with ONE JSON object only (no markdown, no text before or "
                                                  "after it) matching this JSON Schema:\n" + json.dumps(schema))
            r = await self._post(p, body)
            text = r.text[:400]
            low = text.lower()
            if r.status_code in (400, 401, 403) and re.search(r"invalid auth|api key not valid|invalid api key|unauthorized|authentication", low):
                self._block(p, 6 * 3600, "key rejected by the provider")
                continue
            if r.status_code == 400 and p["tok"] == "max_tokens" and "max_completion_tokens" in low:
                p["tok"] = "max_completion_tokens"      # newer OpenAI models renamed the output-limit parameter
                continue
            if r.status_code in (400, 422) and schema is not None and p["mode"] > 0 and not re.search(r"credit|quota|billing|balance|api key", low):
                p["mode"] -= 1                 # provider lacks this JSON mode: step down (schema -> JSON object -> prompt only)
                continue
            if r.status_code in (404, 410):
                self._block(p, 6 * 3600, f"model '{p['model']}' not found for this key")
                continue
            # --- conditions that mean "this key is done for now": move to the next key
            if r.status_code in (401, 403):
                self._block(p, 6 * 3600, f"key rejected (HTTP {r.status_code})")
                continue
            if r.status_code == 402 or re.search(r"insufficient|out of credit|no credit|quota|exceeded your|billing|balance", low):
                self._block(p, 6 * 3600, "out of credit / quota")
                continue
            if r.status_code == 429:
                self._block(p, 90, "rate limited")
                continue
            if r.status_code >= 500:
                raise LLMError(f"LLM provider error {r.status_code}: {text}", retryable=True)
            if r.status_code >= 400:
                raise LLMError(f"LLM provider error {r.status_code}: {text}")
            data = r.json()
            if data.get("error"):
                err = str(data["error"])[:300]
                if re.search(r"credit|quota|rate|limit|billing|balance", err.lower()):
                    self._block(p, 300, err[:80])
                    continue
                raise LLMError(f"LLM provider error: {err}", retryable=True)
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                if grew:
                    raise LLMError("The model's answer was cut off even with a larger limit; raise LLM_MAX_TOKENS in backend/.env.")
                grew, max_tokens = True, min(max(max_tokens * 2, 8000), 16000)   # cut-off answer: one retry with more room
                continue
            content = (choice["message"].get("content") or "").strip()
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()   # reasoning models
            if schema is None:
                if not content:
                    raise LLMError("The model returned an empty answer.", retryable=True)
                return {"text": content}
            m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.S)
            if m:
                content = m.group(1)
            elif not content.startswith("{") and "{" in content:
                content = content[content.index("{"): content.rindex("}") + 1]
            try:
                out = json.loads(content, strict=False)
            except json.JSONDecodeError as e:
                raise LLMError("Model returned malformed JSON.", retryable=True) from e
            missing = [k for k in schema.get("required", []) if k not in out]
            if missing:
                raise LLMError(f"Model response is missing fields: {', '.join(missing)}.", retryable=True)
            return out
        raise LLMError("The LLM provider kept failing; see the engine log.", retryable=True)


def make_llm(s: Settings) -> LLMProvider:
    if s.llm_provider == "openai_compatible":
        return OpenAICompatibleProvider(s)
    return AnthropicProvider(s)


async def generate_with_retry(llm: LLMProvider, system: str, user: str, schema: dict | None, *, attempts: int = 3, effort: str | None = None) -> dict:
    delay = 4.0
    for i in range(attempts):
        try:
            return await llm.generate_json(system, user, schema, effort=effort)
        except LLMError as e:
            if not e.retryable or i == attempts - 1:
                raise
            log.warning("LLM call failed (%s); retrying in %.0fs", e, delay)
            await asyncio.sleep(delay)
            delay *= 2
    raise LLMError("unreachable")
