"""add RAG columns: eval_cases.retrieved_contexts, eval_results.sub_metrics + retrieved_contexts (Phase 8)

Three new JSON columns to support the RAG-aware evaluator path:

* ``eval_cases.retrieved_contexts`` — gold/reference contexts used as
  ``reference_contexts`` for ragas ``context_precision_with_reference`` and
  ``context_recall``. NOT NULL with a default of ``[]`` so generic / agent
  cases pass through untouched.
* ``eval_results.sub_metrics`` — per-metric breakdown (e.g.
  ``{"faithfulness": 0.8, "context_precision": 0.7, "answer_relevance": 0.9}``)
  feeding the gate's nested ``quality.sub_metrics`` axis. Nullable; only RAG
  evaluator writes it.
* ``eval_results.retrieved_contexts`` — the contexts the candidate's
  retriever actually returned at run time, kept for badcase audit. Nullable;
  only RAG evaluator writes it.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    # SQLite needs batch_alter_table; PG accepts plain add_column. We use
    # batch on both for parity with the rest of this codebase.
    with op.batch_alter_table("eval_cases") as batch:
        batch.add_column(
            sa.Column(
                "retrieved_contexts",
                _jsonb(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )

    with op.batch_alter_table("eval_results") as batch:
        batch.add_column(sa.Column("sub_metrics", _jsonb(), nullable=True))
        batch.add_column(sa.Column("retrieved_contexts", _jsonb(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("eval_results") as batch:
        batch.drop_column("retrieved_contexts")
        batch.drop_column("sub_metrics")

    with op.batch_alter_table("eval_cases") as batch:
        batch.drop_column("retrieved_contexts")
