"""Shared FastAPI dependencies (session + auth).

Centralises the request-scoped DB session and the API-key guard so every
router imports one ``SessionDep`` instead of redeclaring it, and the auth
policy lives in exactly one place.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.core.config import get_settings
from evalgate.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def require_api_key(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    """Enforce the API key when one is configured.

    No key configured (local/dev default) => auth disabled, everything passes.
    Otherwise require it via ``Authorization: Bearer <key>`` or ``X-API-Key``.
    """
    expected = get_settings().api_key
    if not expected:
        return
    provided = x_api_key
    if not provided and authorization:
        scheme, _, token = authorization.partition(" ")
        provided = token.strip() if scheme.lower() == "bearer" else authorization.strip()
    # Constant-time compare so a timing side-channel can't leak the key byte by
    # byte. ``compare_digest`` needs a real string, not ``None``.
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


AuthDep = Depends(require_api_key)
