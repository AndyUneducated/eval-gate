"""Eval set persistence layer.

All eval-set / eval-case mutations live here so the API router and the CLI
share one implementation. Kept dialect-agnostic (no `pg_insert`); SQLite
test fixtures use the same code path as production Postgres.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.core.schemas import TaskKind
from evalgate.db.models import EvalCaseRow, EvalSetRow
from evalgate.ingest import persistence
from evalgate.ingest.case_extract import NoLLMSpanError, extract_case_from_trace


class EvalSetNotFoundError(LookupError):
    """Raised when a set lookup by id-or-name fails to resolve."""


class TraceNotFoundError(LookupError):
    """Raised when promote-from-trace targets a missing trace."""


def _new_id() -> str:
    return uuid4().hex


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


async def list_cases(session: AsyncSession, set_id: str) -> list[EvalCaseRow]:
    stmt = (
        select(EvalCaseRow)
        .where(EvalCaseRow.eval_set_id == set_id)
        .order_by(EvalCaseRow.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def add_case(
    session: AsyncSession,
    *,
    set_id: str,
    task_type: str | TaskKind = TaskKind.generic,
    input: dict[str, Any],
    expected: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    source_trace_id: str | None = None,
    source_span_id: str | None = None,
) -> EvalCaseRow:
    set_row = await session.get(EvalSetRow, set_id)
    if set_row is None:
        raise EvalSetNotFoundError(f"no eval_set with id {set_id!r}")
    row = EvalCaseRow(
        id=_new_id(),
        eval_set_id=set_id,
        task_type=str(task_type),
        input=dict(input),
        expected=dict(expected) if expected is not None else None,
        tags=list(tags or []),
        source_trace_id=source_trace_id,
        source_span_id=source_span_id,
    )
    session.add(row)
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
    "resolve_set_id",
]
