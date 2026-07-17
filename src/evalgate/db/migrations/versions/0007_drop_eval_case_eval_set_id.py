"""fold eval_cases.eval_set_id into eval_case_set_memberships (Phase 4.5)

The legacy Phase 4 N:1 column ``eval_cases.eval_set_id`` becomes redundant
once promote uses the membership join (Phase 7.5). This migration:

1. Backfills one membership row per existing case (skipping any that
   already have a matching (case, set) membership).
2. Drops the index + FK + column from ``eval_cases``.

After this point, ``eval_case_set_memberships`` is the single source of
truth for "which sets does this case live in".

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-15
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _membership_table() -> sa.Table:
    """Lightweight Table handle (just the columns we touch)."""
    return sa.table(
        "eval_case_set_memberships",
        sa.column("id", sa.String()),
        sa.column("eval_case_id", sa.String()),
        sa.column("eval_set_id", sa.String()),
        sa.column("promoted_from_result_id", sa.String()),
        sa.column("strategy", sa.String()),
        sa.column("tags", sa.JSON()),
    )


def upgrade() -> None:
    bind = op.get_bind()
    cases = bind.execute(
        sa.text("SELECT id, eval_set_id FROM eval_cases")
    ).fetchall()

    existing = {
        (row.eval_case_id, row.eval_set_id)
        for row in bind.execute(
            sa.text(
                "SELECT eval_case_id, eval_set_id FROM eval_case_set_memberships"
            )
        )
    }

    to_insert = [
        {
            "id": uuid.uuid4().hex,
            "eval_case_id": case.id,
            "eval_set_id": case.eval_set_id,
            "promoted_from_result_id": None,
            "strategy": None,
            "tags": [],
        }
        for case in cases
        if (case.id, case.eval_set_id) not in existing
    ]
    if to_insert:
        op.bulk_insert(_membership_table(), to_insert)

    # SQLite needs batch_alter_table to drop a column with an FK + index.
    with op.batch_alter_table("eval_cases") as batch:
        batch.drop_index("ix_eval_cases_eval_set_id")
        batch.drop_column("eval_set_id")


def downgrade() -> None:
    # Re-create column (nullable so existing rows pass), then backfill
    # from membership (taking the *oldest* membership as the primary set
    # to mirror the original Phase 4 semantics), then enforce NOT NULL +
    # FK + index.
    with op.batch_alter_table("eval_cases") as batch:
        batch.add_column(sa.Column("eval_set_id", sa.String(), nullable=True))

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE eval_cases
            SET eval_set_id = (
                SELECT m.eval_set_id
                FROM eval_case_set_memberships AS m
                WHERE m.eval_case_id = eval_cases.id
                ORDER BY m.created_at ASC
                LIMIT 1
            )
            """
        )
    )

    # Cases with zero memberships can't be represented by the legacy N:1 schema
    # (which required a set). Drop them so the NOT NULL enforcement below can't
    # abort the downgrade mid-way on a leftover NULL.
    bind.execute(sa.text("DELETE FROM eval_cases WHERE eval_set_id IS NULL"))

    with op.batch_alter_table("eval_cases") as batch:
        batch.alter_column("eval_set_id", nullable=False)
        batch.create_foreign_key(
            "fk_eval_cases_eval_set_id_eval_sets",
            "eval_sets",
            ["eval_set_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index(
            "ix_eval_cases_eval_set_id", ["eval_set_id"]
        )
