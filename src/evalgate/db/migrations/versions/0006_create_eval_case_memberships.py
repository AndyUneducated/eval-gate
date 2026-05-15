"""create eval_case_set_memberships table (Phase 7.5)

Replaces the Phase 7 "copy EvalCaseRow on promote" model with a proper
many-to-many membership join. Phase 4.5 (migration 0007) then folds
``EvalCaseRow.eval_set_id`` *into* this table as the single source of
truth for case-to-set membership.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_case_set_memberships",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "eval_case_id",
            sa.String(),
            sa.ForeignKey("eval_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "eval_set_id",
            sa.String(),
            sa.ForeignKey("eval_sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("promoted_from_result_id", sa.String(), nullable=True),
        sa.Column("strategy", sa.String(), nullable=True),
        sa.Column(
            "tags",
            sa.JSON().with_variant(
                sa.dialects.postgresql.JSONB(), "postgresql"
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "eval_case_id",
            "eval_set_id",
            name="uq_eval_case_set_memberships_case_set",
        ),
    )
    op.create_index(
        "ix_eval_case_set_memberships_eval_case_id",
        "eval_case_set_memberships",
        ["eval_case_id"],
    )
    op.create_index(
        "ix_eval_case_set_memberships_eval_set_id",
        "eval_case_set_memberships",
        ["eval_set_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_eval_case_set_memberships_eval_set_id",
        table_name="eval_case_set_memberships",
    )
    op.drop_index(
        "ix_eval_case_set_memberships_eval_case_id",
        table_name="eval_case_set_memberships",
    )
    op.drop_table("eval_case_set_memberships")
