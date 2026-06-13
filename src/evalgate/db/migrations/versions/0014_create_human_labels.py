"""create human_labels (Phase 16)

Phase 16 Judge Calibration needs (judge_score, human_label) pairs as ground
truth. ``human_labels`` stores a binary human verdict (``good`` / ``bad``) on a
judged result; ``eval_result_id`` is a soft reference (no FK) so labels survive
result/run deletion. The same table is the intended Phase 17 Cohen's kappa
source.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "human_labels",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("eval_result_id", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("annotator", sa.String(), nullable=False, server_default="human"),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_human_labels_eval_result_id", "human_labels", ["eval_result_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_human_labels_eval_result_id", table_name="human_labels")
    op.drop_table("human_labels")
