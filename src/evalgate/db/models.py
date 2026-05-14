"""ORM mappings. JSON columns use Postgres JSONB but fall back to plain JSON
for other dialects so tests can run without a Postgres instance.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String
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
