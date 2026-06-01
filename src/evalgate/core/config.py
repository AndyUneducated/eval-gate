"""Runtime configuration, loaded once from environment / .env."""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def is_mock_llm() -> bool:
    """True when ``EVALGATE_MOCK_LLM`` is set (offline / CI mode)."""
    return os.environ.get("EVALGATE_MOCK_LLM", "").lower() in {"1", "true", "yes"}


class Settings(BaseSettings):
    """All app-wide settings.

    Values are read from environment variables (case-insensitive) with optional
    `.env` fallback. Keep this surface intentionally small: each new key is a
    deploy-time contract.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: str = Field(default="local")
    log_level: str = Field(default="INFO")

    database_url: str = Field(
        default="postgresql+asyncpg://evalgate:evalgate@localhost:5433/evalgate",
    )

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
