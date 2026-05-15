"""Span / trace persistence — single source of truth shared by the simple
ingest endpoint and the OTLP endpoint.

Behavior:

* Span inserts are **idempotent on `span_id`** — the same exporter retrying or
  the same payload being replayed is safe. Conflicting rows are left as-is
  (first-write-wins for span bodies; this matches how OTel SDKs assume
  immutable span emission).
* Trace rows are an **aggregation rollup**: per `trace_id` we keep
  `start_time = min(span.start_time)`, `end_time = max(span.end_time)`,
  `span_count`, the first non-parented span seen as `root_span_id`, plus
  the resource attributes from the payload. On replay or partial trace
  delivery, the rollup is merged with whatever's already in the row.

The on-conflict clause is dialect-aware so the same code runs on Postgres
(production) and SQLite (unit tests).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.core.schemas import Span
from evalgate.db.models import SpanRow, TraceRow


def _bulk_upsert_spans(session: AsyncSession, rows: list[dict[str, Any]]):
    """Return a dialect-appropriate INSERT … ON CONFLICT DO NOTHING for spans."""
    dialect = session.bind.dialect.name if session.bind else "postgresql"
    if dialect == "sqlite":
        stmt = sqlite_insert(SpanRow).values(rows)
        return stmt.on_conflict_do_nothing(index_elements=["span_id"])
    stmt = pg_insert(SpanRow).values(rows)
    return stmt.on_conflict_do_nothing(index_elements=["span_id"])


def _trace_upsert(session: AsyncSession, row: dict[str, Any]):
    """Return a dialect-appropriate UPSERT statement for the `traces` rollup.

    The caller has already recomputed `start_time` / `end_time` / `span_count`
    from the authoritative `spans` table for this trace, so on conflict we
    just overwrite with the freshly-computed values. `root_span_id` /
    `service_name` / `resource_attributes` are kept stable across replays
    (first non-null wins).
    """
    dialect = session.bind.dialect.name if session.bind else "postgresql"
    base_set = {
        "start_time": "start_time",
        "end_time": "end_time",
        "span_count": "span_count",
    }
    if dialect == "sqlite":
        stmt = sqlite_insert(TraceRow).values(row)
        return stmt.on_conflict_do_update(
            index_elements=["trace_id"],
            set_={
                **{k: getattr(stmt.excluded, v) for k, v in base_set.items()},
                "root_span_id": func.coalesce(TraceRow.root_span_id, stmt.excluded.root_span_id),
                "service_name": func.coalesce(TraceRow.service_name, stmt.excluded.service_name),
            },
        )
    stmt = pg_insert(TraceRow).values(row)
    return stmt.on_conflict_do_update(
        index_elements=["trace_id"],
        set_={
            **{k: getattr(stmt.excluded, v) for k, v in base_set.items()},
            "root_span_id": func.coalesce(TraceRow.root_span_id, stmt.excluded.root_span_id),
            "service_name": func.coalesce(TraceRow.service_name, stmt.excluded.service_name),
            "resource_attributes": func.coalesce(
                stmt.excluded.resource_attributes, TraceRow.resource_attributes
            ),
        },
    )


async def persist_spans(
    session: AsyncSession,
    spans: list[Span],
    resource_attrs: dict[str, Any] | None = None,
) -> list[str]:
    """Idempotently write spans + their rolled-up trace rows. Returns the
    distinct trace ids touched by this call (sorted)."""
    if not spans:
        return []

    span_rows = [
        {
            "span_id": s.span_id,
            "trace_id": s.trace_id,
            "parent_span_id": s.parent_span_id,
            "name": s.name,
            "kind": str(s.kind),
            "start_time": s.start_time,
            "end_time": s.end_time,
            "attributes": dict(s.attributes),
            "status_code": s.status_code,
            "status_message": s.status_message,
        }
        for s in spans
    ]
    await session.execute(_bulk_upsert_spans(session, span_rows))

    by_trace: dict[str, list[Span]] = defaultdict(list)
    for s in spans:
        by_trace[s.trace_id].append(s)

    service_name = None
    if resource_attrs:
        service_name = resource_attrs.get("service.name") or resource_attrs.get("service_name")

    # Read back the authoritative aggregate from `spans` so replays /
    # out-of-order partial deliveries always converge to the right rollup
    # rather than double-counting.
    for trace_id, group in by_trace.items():
        agg_stmt = select(
            func.min(SpanRow.start_time),
            func.max(SpanRow.end_time),
            func.count(SpanRow.span_id),
        ).where(SpanRow.trace_id == trace_id)
        start_time, end_time, span_count = (await session.execute(agg_stmt)).one()

        root = next((s for s in group if not s.parent_span_id), None)
        row = {
            "trace_id": trace_id,
            "root_span_id": root.span_id if root else None,
            "service_name": service_name,
            "start_time": start_time,
            "end_time": end_time,
            "span_count": int(span_count),
            "resource_attributes": dict(resource_attrs or {}),
        }
        await session.execute(_trace_upsert(session, row))

    await session.commit()
    return sorted(by_trace.keys())


async def list_traces(
    session: AsyncSession,
    limit: int = 50,
    since=None,
    service: str | None = None,
) -> list[TraceRow]:
    stmt = select(TraceRow).order_by(TraceRow.start_time.desc()).limit(limit)
    if since is not None:
        stmt = stmt.where(TraceRow.start_time >= since)
    if service:
        stmt = stmt.where(TraceRow.service_name == service)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_trace(session: AsyncSession, trace_id: str) -> tuple[TraceRow | None, list[SpanRow]]:
    trace = await session.get(TraceRow, trace_id)
    if trace is None:
        return None, []
    span_stmt = select(SpanRow).where(SpanRow.trace_id == trace_id).order_by(SpanRow.start_time)
    spans = list((await session.execute(span_stmt)).scalars().all())
    return trace, spans
