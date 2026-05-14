"""create spans table

Revision ID: 0001
Revises:
Create Date: 2026-05-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "spans",
        sa.Column("span_id", sa.String(), primary_key=True),
        sa.Column("trace_id", sa.String(), nullable=False),
        sa.Column("parent_span_id", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False, server_default="other"),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attributes",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status_code", sa.String(), nullable=False, server_default="OK"),
        sa.Column("status_message", sa.String(), nullable=True),
    )
    op.create_index("ix_spans_trace_id", "spans", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_spans_trace_id", table_name="spans")
    op.drop_table("spans")
