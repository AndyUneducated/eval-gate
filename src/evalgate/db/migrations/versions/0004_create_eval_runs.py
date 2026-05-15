"""create eval_runs + eval_results tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "eval_set_id",
            sa.String(),
            sa.ForeignKey("eval_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("prompt_path", sa.String(), nullable=False),
        sa.Column("prompt_hash", sa.String(), nullable=False),
        sa.Column("candidate_model", sa.String(), nullable=False),
        sa.Column("judge_model", sa.String(), nullable=False),
        sa.Column("total_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mean_score", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_eval_runs_eval_set_id", "eval_runs", ["eval_set_id"])

    op.create_table(
        "eval_results",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "eval_run_id",
            sa.String(),
            sa.ForeignKey("eval_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("eval_case_id", sa.String(), nullable=True),
        sa.Column("tags", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("output", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "safety_violation",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("judge_confidence", sa.Float(), nullable=True),
        sa.Column("judge_raw", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_eval_results_eval_run_id", "eval_results", ["eval_run_id"])
    op.create_index("ix_eval_results_eval_case_id", "eval_results", ["eval_case_id"])


def downgrade() -> None:
    op.drop_index("ix_eval_results_eval_case_id", table_name="eval_results")
    op.drop_index("ix_eval_results_eval_run_id", table_name="eval_results")
    op.drop_table("eval_results")
    op.drop_index("ix_eval_runs_eval_set_id", table_name="eval_runs")
    op.drop_table("eval_runs")
