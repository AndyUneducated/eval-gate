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

    # Phase 13 Shadow Mode: Slack-compatible incoming-webhook URL. When a
    # rolling shadow report shows a regressed axis, ``shadow.alert`` POSTs a
    # ``{"text": ...}`` payload here. Unset -> alerts degrade to a structlog
    # warning (demo / local default).
    shadow_webhook_url: str | None = Field(
        default=None,
        validation_alias="EVALGATE_SHADOW_WEBHOOK_URL",
    )

    # Phase 14 Adversarial Synth: the cheap generator model the red-team
    # synthesizer uses to author tricky cases. Defaults to the same small model
    # the badcase finder uses; overridable via env or CLI ``--model``.
    adversarial_generator_model: str = Field(
        default="ollama/qwen3.5:9b",
        validation_alias="EVALGATE_ADVERSARIAL_GENERATOR_MODEL",
    )

    # Phase 16 Judge Calibration: where the fitted temperature-scaling params
    # live on disk. ``evalgate calibration fit`` writes it; ``report`` and the
    # badcase ``--calibration`` path read it. Raw scores stay immutable in the
    # DB — calibration is applied read-time from this file.
    calibration_params_path: str = Field(
        default="calibration_params.json",
        validation_alias="EVALGATE_CALIBRATION_PARAMS_PATH",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
