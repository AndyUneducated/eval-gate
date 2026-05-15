"""create eval_judge_calls table

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_judge_calls",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "eval_result_id",
            sa.String(),
            sa.ForeignKey("eval_results.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("judge_model", sa.String(), nullable=False),
        sa.Column("sub_run_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("position", sa.String(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("winner", sa.String(), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("raw", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_eval_judge_calls_eval_result_id",
        "eval_judge_calls",
        ["eval_result_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_eval_judge_calls_eval_result_id", table_name="eval_judge_calls")
    op.drop_table("eval_judge_calls")
