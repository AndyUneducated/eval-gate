"""create traces rollup table

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "traces",
        sa.Column("trace_id", sa.String(), primary_key=True),
        sa.Column("root_span_id", sa.String(), nullable=True),
        sa.Column("service_name", sa.String(), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("span_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "resource_attributes",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index("ix_traces_start_time", "traces", ["start_time"])
    op.create_index("ix_traces_service_name", "traces", ["service_name"])


def downgrade() -> None:
    op.drop_index("ix_traces_service_name", table_name="traces")
    op.drop_index("ix_traces_start_time", table_name="traces")
    op.drop_table("traces")
