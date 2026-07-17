"""Eval set persistence layer.

All eval-set / eval-case mutations live here so the API router and the CLI
share one implementation. Kept dialect-agnostic (no `pg_insert`); SQLite
test fixtures use the same code path as production Postgres.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.core.errors import EvalGateError
from evalgate.core.schemas import CaseSource, CaseStatus, TaskKind
from evalgate.db.models import EvalCaseRow, EvalCaseSetMembershipRow, EvalSetRow
from evalgate.db.query_helpers import new_id as _new_id
from evalgate.ingest import persistence
from evalgate.ingest.case_extract import NoLLMSpanError, extract_case_from_trace


class EvalSetNotFoundError(EvalGateError, LookupError):
    """Raised when a set lookup by id-or-name fails to resolve."""

    http_status = 404
    exit_code = 1
    slug = "eval_set_not_found"


class TraceNotFoundError(EvalGateError, LookupError):
    """Raised when promote-from-trace targets a missing trace."""

    http_status = 404
    exit_code = 1
    slug = "trace_not_found"


async def create_eval_set(
    session: AsyncSession,
    *,
    name: str,
    description: str | None = None,
) -> EvalSetRow:
    row = EvalSetRow(id=_new_id(), name=name, description=description)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_eval_sets(
    session: AsyncSession,
    *,
    limit: int = 50,
    since: datetime | None = None,
) -> list[EvalSetRow]:
    stmt = select(EvalSetRow).order_by(EvalSetRow.created_at.desc()).limit(limit)
    if since is not None:
        stmt = stmt.where(EvalSetRow.created_at >= since)
    return list((await session.execute(stmt)).scalars().all())


async def get_eval_set(session: AsyncSession, set_id: str) -> EvalSetRow | None:
    return await session.get(EvalSetRow, set_id)


async def resolve_set_id(session: AsyncSession, identifier: str) -> str:
    """Accept a UUID hex *or* a set name. Name-resolution returns the most
    recently created set with that name (since names are not unique)."""
    direct = await session.get(EvalSetRow, identifier)
    if direct is not None:
        return direct.id
    stmt = (
        select(EvalSetRow)
        .where(EvalSetRow.name == identifier)
        .order_by(EvalSetRow.created_at.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise EvalSetNotFoundError(f"no eval_set with id or name {identifier!r}")
    return row.id


_ACTIVE_ONLY: tuple[str, ...] = (CaseStatus.active.value,)


async def list_cases(
    session: AsyncSession,
    set_id: str,
    *,
    statuses: tuple[str, ...] | None = _ACTIVE_ONLY,
) -> list[EvalCaseRow]:
    """Return cases that belong to ``set_id``.

    Single source of truth (Phase 4.5): every case lives in a set via
    exactly one ``EvalCaseSetMembershipRow`` row (or more, if it was
    promoted into additional sets). Order by case ``created_at`` ascending
    so Phase 5 runner iteration stays deterministic.

    Phase 14: ``statuses`` filters on ``EvalCaseRow.status`` and defaults to
    ``("active",)`` — so the runner / gate never see ``pending`` (awaiting
    review) or ``archived`` (rejected) cases. Pass ``statuses=None`` to return
    every case regardless of status (used by detail / show views).
    """
    stmt = (
        select(EvalCaseRow)
        .join(
            EvalCaseSetMembershipRow,
            EvalCaseSetMembershipRow.eval_case_id == EvalCaseRow.id,
        )
        .where(EvalCaseSetMembershipRow.eval_set_id == set_id)
        .order_by(EvalCaseRow.created_at)
    )
    if statuses is not None:
        stmt = stmt.where(EvalCaseRow.status.in_(statuses))
    return list((await session.execute(stmt)).scalars().all())


async def list_memberships(
    session: AsyncSession, *, set_id: str | None = None, case_id: str | None = None
) -> list[EvalCaseSetMembershipRow]:
    """Inspect raw memberships for a set or a case (audit + Phase 7 tests)."""
    stmt = select(EvalCaseSetMembershipRow)
    if set_id is not None:
        stmt = stmt.where(EvalCaseSetMembershipRow.eval_set_id == set_id)
    if case_id is not None:
        stmt = stmt.where(EvalCaseSetMembershipRow.eval_case_id == case_id)
    stmt = stmt.order_by(EvalCaseSetMembershipRow.created_at)
    return list((await session.execute(stmt)).scalars().all())


async def add_case(
    session: AsyncSession,
    *,
    set_id: str,
    task_type: str | TaskKind = TaskKind.generic,
    input: dict[str, Any],
    expected: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    status: str | CaseStatus = CaseStatus.active,
    source: str | CaseSource = CaseSource.manual,
    retrieved_contexts: list[str] | None = None,
    expected_trajectory: list[dict[str, Any]] | None = None,
    source_trace_id: str | None = None,
    source_span_id: str | None = None,
) -> EvalCaseRow:
    set_row = await session.get(EvalSetRow, set_id)
    if set_row is None:
        raise EvalSetNotFoundError(f"no eval_set with id {set_id!r}")
    row = EvalCaseRow(
        id=_new_id(),
        task_type=str(task_type),
        status=str(status),
        source=str(source),
        input=dict(input),
        expected=dict(expected) if expected is not None else None,
        tags=list(tags or []),
        retrieved_contexts=[str(c) for c in (retrieved_contexts or [])],
        expected_trajectory=[
            dict(step) if isinstance(step, dict) else {"raw": step}
            for step in (expected_trajectory or [])
        ],
        source_trace_id=source_trace_id,
        source_span_id=source_span_id,
    )
    membership = EvalCaseSetMembershipRow(
        id=_new_id(),
        eval_case_id=row.id,
        eval_set_id=set_id,
        promoted_from_result_id=None,
        strategy=None,
        tags=[],
    )
    session.add(row)
    session.add(membership)
    await session.commit()
    await session.refresh(row)
    return row


async def add_case_from_trace(
    session: AsyncSession,
    *,
    set_id: str,
    trace_id: str,
    extra_tags: list[str] | None = None,
    task_type_override: TaskKind | str | None = None,
) -> EvalCaseRow:
    set_row = await session.get(EvalSetRow, set_id)
    if set_row is None:
        raise EvalSetNotFoundError(f"no eval_set with id {set_id!r}")

    trace_row, spans = await persistence.get_trace(session, trace_id)
    if trace_row is None:
        raise TraceNotFoundError(f"no trace with id {trace_id!r}")

    override: TaskKind | None = None
    if task_type_override is not None:
        override = (
            task_type_override
            if isinstance(task_type_override, TaskKind)
            else TaskKind(str(task_type_override))
        )

    # `extract_case_from_trace` is purely structural and accepts our ORM rows
    # via the `SpanLike` Protocol.
    payload = extract_case_from_trace(
        list(spans),
        extra_tags=extra_tags,
        task_type_override=override,
    )
    return await add_case(
        session,
        set_id=set_id,
        task_type=payload["task_type"],
        input=payload["input"],
        expected=payload["expected"],
        tags=payload["tags"],
        source=CaseSource.trace,
        retrieved_contexts=payload.get("retrieved_contexts") or [],
        expected_trajectory=payload.get("expected_trajectory") or [],
        source_trace_id=trace_id,
        source_span_id=payload["source_span_id"],
    )


# Re-export for callers that want to catch the extractor error without
# importing from the ingest package directly.
__all__ = [
    "EvalSetNotFoundError",
    "NoLLMSpanError",
    "TraceNotFoundError",
    "add_case",
    "add_case_from_trace",
    "create_eval_set",
    "get_eval_set",
    "list_cases",
    "list_eval_sets",
    "list_memberships",
    "resolve_set_id",
]
