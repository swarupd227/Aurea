"""Central configuration. Everything is environment-driven and has a safe local default,
so the platform boots with `docker compose up` even with no secrets configured."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # ── Identity ──────────────────────────────────────────────────────────────
    app_name: str = "Aurea"
    app_env: str = "local"  # AUREA_ENV
    log_level: str = "INFO"  # AUREA_LOG_LEVEL

    # ── Database / cache ──────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://aurea:aurea@localhost:5432/aurea"
    redis_url: str = "redis://localhost:6379/0"

    # ── Security ──────────────────────────────────────────────────────────────
    jwt_secret: str = "dev-insecure-change-me-please-0123456789abcdef"  # AUREA_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 720

    # ── LLM (Anthropic-first, model-portable) ─────────────────────────────────
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    model_advice: str = "claude-opus-4-8"
    model_narrative: str = "claude-opus-4-8"
    model_classify: str = "claude-haiku-4-5-20251001"
    openai_model: str = "gpt-4o"

    # ── Market data ───────────────────────────────────────────────────────────
    marketdata_provider: str = "yahoo"
    alphavantage_api_key: str = ""

    # ── Frontend ──────────────────────────────────────────────────────────────
    frontend_url: str = "http://localhost:3010"

    # ── Behaviour ─────────────────────────────────────────────────────────────
    run_seed: bool = True
    role: str = "api"  # api | worker

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key or self.openai_api_key)


# Field-name → env-var aliases (so AUREA_ENV maps to app_env, etc.).
_ENV_ALIASES = {
    "app_env": "AUREA_ENV",
    "log_level": "AUREA_LOG_LEVEL",
    "jwt_secret": "AUREA_JWT_SECRET",
    "access_token_ttl_minutes": "AUREA_ACCESS_TOKEN_TTL_MINUTES",
    "model_advice": "AUREA_MODEL_ADVICE",
    "model_narrative": "AUREA_MODEL_NARRATIVE",
    "model_classify": "AUREA_MODEL_CLASSIFY",
    "openai_model": "AUREA_OPENAI_MODEL",
    "marketdata_provider": "AUREA_MARKETDATA_PROVIDER",
    "run_seed": "AUREA_RUN_SEED",
    "role": "AUREA_ROLE",
}


@lru_cache
def get_settings() -> Settings:
    import os

    # Resolve aliased env vars before constructing Settings.
    overrides = {}
    for field, env_name in _ENV_ALIASES.items():
        if env_name in os.environ:
            overrides[field] = os.environ[env_name]
    return Settings(**overrides)


settings = get_settings()
