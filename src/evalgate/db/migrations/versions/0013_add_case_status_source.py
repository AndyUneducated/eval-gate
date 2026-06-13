"""add eval_cases.status + eval_cases.source (Phase 14)

Phase 14 Adversarial Synth introduces a review lifecycle for eval cases. The
red-team synthesizer writes new cases as ``status='pending'`` so they cannot
enter a gate run before a human approves them (``active``) or rejects them
(``archived``). ``source`` records provenance (``manual`` / ``trace`` /
``adversarial``).

Existing rows default to ``active`` + ``manual``; rows that were promoted from a
captured trace (``source_trace_id IS NOT NULL``) are backfilled to
``source='trace'`` so historical provenance is preserved.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "eval_cases",
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
    )
    op.add_column(
        "eval_cases",
        sa.Column("source", sa.String(), nullable=False, server_default="manual"),
    )
    op.create_index("ix_eval_cases_status", "eval_cases", ["status"])
    # Backfill provenance for trace-promoted cases.
    op.execute(
        sa.text(
            "UPDATE eval_cases SET source = 'trace' WHERE source_trace_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_eval_cases_status", table_name="eval_cases")
    op.drop_column("eval_cases", "source")
    op.drop_column("eval_cases", "status")
