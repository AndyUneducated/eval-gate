"""ORM mappings. JSON columns use Postgres JSONB but fall back to plain JSON
for other dialects so tests can run without a Postgres instance.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


JsonType = JSON().with_variant(JSONB(), "postgresql")


class SpanRow(Base):
    __tablename__ = "spans"

    span_id: Mapped[str] = mapped_column(String, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    parent_span_id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="other")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    status_code: Mapped[str] = mapped_column(String, nullable=False, default="OK")
    status_message: Mapped[str | None] = mapped_column(String, nullable=True)


class TraceRow(Base):
    """Per-trace rollup: lets `GET /v1/traces` paginate without scanning spans."""

    __tablename__ = "traces"

    trace_id: Mapped[str] = mapped_column(String, primary_key=True)
    root_span_id: Mapped[str | None] = mapped_column(String, nullable=True)
    service_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    span_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resource_attributes: Mapped[dict[str, Any]] = mapped_column(
        JsonType, default=dict, nullable=False
    )


class EvalSetRow(Base):
    """A named collection of eval cases. Cheap to create — one per concern
    (e.g. "billing-regress-cases", "rag-faithfulness")."""

    __tablename__ = "eval_sets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class EvalCaseRow(Base):
    """A single (input, expected) data point inside an eval set.

    `source_trace_id` is a *soft* reference (no FK) — eval cases must outlive
    their source trace (which may be archived / pruned by retention).
    """

    __tablename__ = "eval_cases"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    eval_set_id: Mapped[str] = mapped_column(
        String, ForeignKey("eval_sets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_type: Mapped[str] = mapped_column(String, nullable=False, default="generic")
    input: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    expected: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    source_trace_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    source_span_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
