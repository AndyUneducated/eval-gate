"""Phase 10: migration 0010 reshape logic round-trip on SQLite.

The rest of the suite bootstraps schema directly via SQLAlchemy metadata
(see ``conftest.py``) because earlier migrations rely on ``JSONB`` and
don't run on SQLite. Here we build a minimal stand-in ``eval_results``
table at the pre-0010 state, then invoke the migration module's
``upgrade()`` / ``downgrade()`` functions via :class:`alembic.operations.Operations`
to exercise the data reshape and DDL on the same dialect ``conftest`` uses.
"""

from __future__ import annotations

import importlib
import json
import uuid

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION_MOD = "evalgate.db.migrations.versions.0010_axis_breakdown"


def _build_pre_0010_table(engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "eval_results",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("eval_run_id", sa.String, nullable=False),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("sub_metrics", sa.JSON, nullable=True),
    )
    metadata.create_all(engine)


def _seed_row(engine, *, row_id: str, sub: dict | None) -> None:
    payload = json.dumps(sub) if sub is not None else None
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO eval_results (id, eval_run_id, score, sub_metrics) "
                "VALUES (:id, 'run', 0.5, :sub)"
            ),
            {"id": row_id, "sub": payload},
        )


def _column_names(engine, table: str) -> set[str]:
    with engine.begin() as conn:
        rows = conn.execute(sa.text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


@pytest.mark.parametrize("with_payload", [True, False])
def test_migration_0010_round_trip(tmp_path, with_payload: bool):
    db_path = tmp_path / "mig.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    _build_pre_0010_table(engine)

    legacy = {"faithfulness": 0.9, "context_precision": 0.8, "answer_relevance": 1.0}
    row_id = uuid.uuid4().hex
    _seed_row(engine, row_id=row_id, sub=legacy if with_payload else None)

    migration = importlib.import_module(MIGRATION_MOD)

    # --- upgrade ---
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.upgrade()

    cols = _column_names(engine, "eval_results")
    assert "axis_breakdown" in cols
    assert "sub_metrics" not in cols

    with engine.begin() as conn:
        stored = conn.execute(
            sa.text("SELECT axis_breakdown FROM eval_results WHERE id = :id"),
            {"id": row_id},
        ).scalar()
    if isinstance(stored, str):
        stored = json.loads(stored)
    if with_payload:
        assert stored == {"quality": legacy}
    else:
        assert stored is None

    # --- downgrade ---
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.downgrade()

    cols = _column_names(engine, "eval_results")
    assert "sub_metrics" in cols
    assert "axis_breakdown" not in cols

    with engine.begin() as conn:
        stored = conn.execute(
            sa.text("SELECT sub_metrics FROM eval_results WHERE id = :id"),
            {"id": row_id},
        ).scalar()
    if isinstance(stored, str):
        stored = json.loads(stored)
    if with_payload:
        assert stored == legacy
    else:
        assert stored is None

    engine.dispose()
