"""API-key auth dependency: disabled by default, enforced once configured."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from evalgate.api import deps


class _Settings:
    def __init__(self, api_key: str | None) -> None:
        self.api_key = api_key


@pytest.mark.asyncio
async def test_auth_disabled_when_no_key(monkeypatch) -> None:
    monkeypatch.setattr(deps, "get_settings", lambda: _Settings(None))
    # No key configured -> everything passes (local/dev default).
    await deps.require_api_key(authorization=None, x_api_key=None)


@pytest.mark.asyncio
async def test_auth_rejects_missing_key(monkeypatch) -> None:
    monkeypatch.setattr(deps, "get_settings", lambda: _Settings("secret"))
    with pytest.raises(HTTPException) as exc:
        await deps.require_api_key(authorization=None, x_api_key=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_auth_accepts_bearer_token(monkeypatch) -> None:
    monkeypatch.setattr(deps, "get_settings", lambda: _Settings("secret"))
    await deps.require_api_key(authorization="Bearer secret", x_api_key=None)


@pytest.mark.asyncio
async def test_auth_accepts_x_api_key_header(monkeypatch) -> None:
    monkeypatch.setattr(deps, "get_settings", lambda: _Settings("secret"))
    await deps.require_api_key(authorization=None, x_api_key="secret")


@pytest.mark.asyncio
async def test_auth_rejects_wrong_key(monkeypatch) -> None:
    monkeypatch.setattr(deps, "get_settings", lambda: _Settings("secret"))
    with pytest.raises(HTTPException):
        await deps.require_api_key(authorization="Bearer nope", x_api_key=None)
