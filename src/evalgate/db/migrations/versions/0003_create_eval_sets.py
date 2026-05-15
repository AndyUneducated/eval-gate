"""create eval_sets + eval_cases tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_sets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_eval_sets_name", "eval_sets", ["name"])

    op.create_table(
        "eval_cases",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "eval_set_id",
            sa.String(),
            sa.ForeignKey("eval_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_type", sa.String(), nullable=False, server_default="generic"),
        sa.Column("input", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("expected", JSONB(), nullable=True),
        sa.Column("tags", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_trace_id", sa.String(), nullable=True),
        sa.Column("source_span_id", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_eval_cases_eval_set_id", "eval_cases", ["eval_set_id"])
    op.create_index("ix_eval_cases_source_trace_id", "eval_cases", ["source_trace_id"])


def downgrade() -> None:
    op.drop_index("ix_eval_cases_source_trace_id", table_name="eval_cases")
    op.drop_index("ix_eval_cases_eval_set_id", table_name="eval_cases")
    op.drop_table("eval_cases")
    op.drop_index("ix_eval_sets_name", table_name="eval_sets")
    op.drop_table("eval_sets")
