"""create shadow_observations + shadow_reports (Phase 13)

Phase 13 Shadow Mode mirrors a fraction of production traffic onto a candidate
prompt. The SDK pushes scored ``(primary, candidate)`` ``EvalRecord`` pairs to
``shadow_observations``; ``shadow.rollup`` periodically aggregates a window of
those into the same 4-axis gate report and snapshots it into ``shadow_reports``
(with an ``alerted`` flag when a regression webhook fired).

Both ``*_record`` / ``report`` columns use JSONB on Postgres and plain JSON on
other dialects, matching ``db.models.JsonType``.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "shadow_observations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("case_id", sa.String(), nullable=False),
        sa.Column("tags", _jsonb(), nullable=False),
        sa.Column("primary_prompt_hash", sa.String(), nullable=False),
        sa.Column("candidate_prompt_hash", sa.String(), nullable=False),
        sa.Column("primary_record", _jsonb(), nullable=False),
        sa.Column("candidate_record", _jsonb(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_shadow_observations_primary_prompt_hash",
        "shadow_observations",
        ["primary_prompt_hash"],
    )
    op.create_index(
        "ix_shadow_observations_candidate_prompt_hash",
        "shadow_observations",
        ["candidate_prompt_hash"],
    )
    op.create_index(
        "ix_shadow_observations_created_at",
        "shadow_observations",
        ["created_at"],
    )

    op.create_table(
        "shadow_reports",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("candidate_prompt_hash", sa.String(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("n_observations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("report", _jsonb(), nullable=False),
        sa.Column("alerted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_shadow_reports_candidate_prompt_hash",
        "shadow_reports",
        ["candidate_prompt_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_shadow_reports_candidate_prompt_hash", table_name="shadow_reports")
    op.drop_table("shadow_reports")
    op.drop_index("ix_shadow_observations_created_at", table_name="shadow_observations")
    op.drop_index("ix_shadow_observations_candidate_prompt_hash", table_name="shadow_observations")
    op.drop_index("ix_shadow_observations_primary_prompt_hash", table_name="shadow_observations")
    op.drop_table("shadow_observations")
