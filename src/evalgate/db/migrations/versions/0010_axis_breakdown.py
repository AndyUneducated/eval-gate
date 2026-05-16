"""rename + reshape eval_results.sub_metrics -> axis_breakdown (Phase 10)

Phase 10 introduces a Safety axis that, like Phase 8's RAG quality breakdown,
needs per-metric sub-axes on the gate report. Rather than carry a parallel
``safety_sub_metrics`` JSON column, we generalise the existing field:

* drop  ``eval_results.sub_metrics``  (was ``dict[str, float]`` keyed by metric)
* add   ``eval_results.axis_breakdown`` (``dict[str, dict[str, float]]``,
  keyed by gate axis name → per-metric value)

Existing rows that carried RAG metrics are reshaped in-place into
``{"quality": <old dict>}`` so demos retain their history. Generic results
that had ``sub_metrics IS NULL`` stay ``axis_breakdown IS NULL``.

The user requirement at Phase 10 was explicit "no backward compatibility
concerns"; we still bother preserving data because it's cheap and keeps
``scripts/phase8_rag_smoke.py`` reproducible across the rename.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-15
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    with op.batch_alter_table("eval_results") as batch:
        batch.add_column(sa.Column("axis_breakdown", _jsonb(), nullable=True))

    if dialect == "postgresql":
        op.execute(
            sa.text(
                """
                UPDATE eval_results
                SET axis_breakdown = jsonb_build_object('quality', sub_metrics)
                WHERE sub_metrics IS NOT NULL
                """
            )
        )
    else:
        # SQLite (and other) path: read rows, reshape in Python, write back.
        rows = bind.execute(
            sa.text(
                "SELECT id, sub_metrics FROM eval_results WHERE sub_metrics IS NOT NULL"
            )
        ).fetchall()
        for row in rows:
            old = row.sub_metrics
            if isinstance(old, str):
                try:
                    old = json.loads(old)
                except json.JSONDecodeError:
                    old = None
            if not isinstance(old, dict) or not old:
                continue
            payload = json.dumps({"quality": old})
            bind.execute(
                sa.text(
                    "UPDATE eval_results SET axis_breakdown = :payload WHERE id = :id"
                ),
                {"payload": payload, "id": row.id},
            )

    with op.batch_alter_table("eval_results") as batch:
        batch.drop_column("sub_metrics")


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    with op.batch_alter_table("eval_results") as batch:
        batch.add_column(sa.Column("sub_metrics", _jsonb(), nullable=True))

    if dialect == "postgresql":
        op.execute(
            sa.text(
                """
                UPDATE eval_results
                SET sub_metrics = axis_breakdown -> 'quality'
                WHERE axis_breakdown IS NOT NULL
                  AND jsonb_typeof(axis_breakdown -> 'quality') = 'object'
                """
            )
        )
    else:
        rows = bind.execute(
            sa.text(
                "SELECT id, axis_breakdown FROM eval_results WHERE axis_breakdown IS NOT NULL"
            )
        ).fetchall()
        for row in rows:
            payload = row.axis_breakdown
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = None
            if not isinstance(payload, dict):
                continue
            quality = payload.get("quality")
            if not isinstance(quality, dict):
                continue
            bind.execute(
                sa.text(
                    "UPDATE eval_results SET sub_metrics = :payload WHERE id = :id"
                ),
                {"payload": json.dumps(quality), "id": row.id},
            )

    with op.batch_alter_table("eval_results") as batch:
        batch.drop_column("axis_breakdown")
