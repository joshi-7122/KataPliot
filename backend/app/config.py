"""Environment-driven configuration. Secrets live only here (backend), never in the dashboard."""
from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    # Server
    host: str = "127.0.0.1"
    port: int = 8765
    data_dir: Path = BACKEND_DIR / "data"
    dashboard_dist: Path = BACKEND_DIR.parent / "dashboard" / "dist"
    # Extra origins allowed to talk to the API (the Vite dev server by default).
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # LLM
    llm_provider: Literal["anthropic", "openai_compatible"] = "anthropic"
    llm_model: str = "claude-opus-5-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    llm_max_tokens: int = 32000
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    # Extra keys tried in order when the current one is out of credit / rate-limited / rejected.
    # Comma-separated. Each entry is `key` or `key|model` or `key|model|base_url`.
    openai_api_keys: str = ""
    # Named fallback providers (both speak the OpenAI-style API). Used in this order after OPENAI_API_KEY.
    openai_official_api_key: str | None = None          # a key from platform.openai.com (not OpenRouter)
    openai_official_model: str = "gpt-4o-mini"
    openai_official_base_url: str = "https://api.openai.com/v1"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    nvidia_api_key: str | None = None
    nvidia_model: str = "nvidia/nemotron-3-ultra-550b-a55b"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"

    # Web search
    search_provider: Literal["tavily", "brave", "serper", "none"] = "none"
    tavily_api_key: str | None = None
    brave_api_key: str | None = None
    serper_api_key: str | None = None
    search_max_results: int = 5
    search_fetch_pages: int = 3

    # Sandbox
    sandbox_backend: Literal["auto", "docker", "seatbelt", "judge0", "none"] = "auto"
    judge0_url: str | None = None
    judge0_api_key: str | None = None
    sandbox_time_limit_s: float = 4.0
    sandbox_compile_time_limit_s: float = 40.0
    sandbox_memory_mb: int = 512

    # Agent
    max_repair_attempts: int = 2
    max_guvi_fix_attempts: int = 8   # auto-submit: how many times to re-fix code that fails GUVI's own Run

    @property
    def db_path(self) -> Path:
        return self.data_dir / "katapilot.db"

    @property
    def origins(self) -> list[str]:
        base = [f"http://localhost:{self.port}", f"http://127.0.0.1:{self.port}"]
        extra = [o.strip() for o in self.allowed_origins.split(",") if o.strip()]
        return base + extra


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s


def get_api_token() -> str:
    """Per-install random token. Pairs the dashboard and extension with this backend so that
    arbitrary websites cannot drive the agent through localhost."""
    path = get_settings().data_dir / "api_token"
    if path.exists():
        return path.read_text().strip()
    token = secrets.token_urlsafe(32)
    path.write_text(token)
    path.chmod(0o600)
    return token
