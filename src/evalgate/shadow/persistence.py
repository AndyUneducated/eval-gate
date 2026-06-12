"""shadow_observations / shadow_reports persistence layer (Phase 13).

Dialect-agnostic (no ``pg_insert``) so the aiosqlite test fixture hits the
same code paths as production Postgres, mirroring ``judge.persistence``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.db.models import ShadowObservationRow, ShadowReportRow


def _new_id() -> str:
    return uuid4().hex


async def add_observation(
    session: AsyncSession,
    *,
    case_id: str,
    tags: list[str],
    primary_prompt_hash: str,
    candidate_prompt_hash: str,
    primary_record: dict[str, Any],
    candidate_record: dict[str, Any],
) -> ShadowObservationRow:
    row = ShadowObservationRow(
        id=_new_id(),
        case_id=case_id,
        tags=list(tags or []),
        primary_prompt_hash=primary_prompt_hash,
        candidate_prompt_hash=candidate_prompt_hash,
        primary_record=dict(primary_record or {}),
        candidate_record=dict(candidate_record or {}),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_observations(
    session: AsyncSession,
    *,
    candidate_prompt_hash: str | None = None,
    since: datetime | None = None,
) -> list[ShadowObservationRow]:
    """List observations oldest-first (so baseline/candidate order is stable).

    ``since`` is an optional DB-side lower bound on ``created_at``;
    ``shadow.rollup`` additionally re-filters in Python to stay robust across
    naive (SQLite) vs tz-aware (Postgres) timestamp storage.
    """
    stmt = select(ShadowObservationRow).order_by(
        ShadowObservationRow.created_at.asc(), ShadowObservationRow.id.asc()
    )
    if candidate_prompt_hash is not None:
        stmt = stmt.where(ShadowObservationRow.candidate_prompt_hash == candidate_prompt_hash)
    if since is not None:
        stmt = stmt.where(ShadowObservationRow.created_at >= since)
    return list((await session.execute(stmt)).scalars().all())


async def add_report(
    session: AsyncSession,
    *,
    candidate_prompt_hash: str,
    window_start: datetime,
    window_end: datetime,
    n_observations: int,
    passed: bool,
    report: dict[str, Any],
    alerted: bool,
) -> ShadowReportRow:
    row = ShadowReportRow(
        id=_new_id(),
        candidate_prompt_hash=candidate_prompt_hash,
        window_start=window_start,
        window_end=window_end,
        n_observations=int(n_observations),
        passed=bool(passed),
        report=dict(report or {}),
        alerted=bool(alerted),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_reports(
    session: AsyncSession,
    *,
    candidate_prompt_hash: str | None = None,
    limit: int = 50,
) -> list[ShadowReportRow]:
    """List recent shadow report snapshots (latest first)."""
    stmt = select(ShadowReportRow).order_by(
        ShadowReportRow.created_at.desc(), ShadowReportRow.id.desc()
    )
    if candidate_prompt_hash is not None:
        stmt = stmt.where(ShadowReportRow.candidate_prompt_hash == candidate_prompt_hash)
    stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().all())
