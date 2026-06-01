"""drop eval_results.safety_violation — safety lives in axis_breakdown only

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("eval_results", "safety_violation")


def downgrade() -> None:
    op.add_column(
        "eval_results",
        sa.Column("safety_violation", sa.Boolean(), nullable=False, server_default="false"),
    )
