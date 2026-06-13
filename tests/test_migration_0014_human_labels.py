"""Phase 16: migration 0014 (create human_labels) round-trip on SQLite."""

from __future__ import annotations

import importlib

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION_MOD = "evalgate.db.migrations.versions.0014_create_human_labels"


def _tables(engine) -> set[str]:
    with engine.begin() as conn:
        rows = conn.execute(sa.text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
    return {r[0] for r in rows}


def test_migration_0014_round_trip(tmp_path):
    db_path = tmp_path / "mig.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    migration = importlib.import_module(MIGRATION_MOD)

    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.upgrade()

    assert "human_labels" in _tables(engine)

    # Insert + read back a row (server_default annotator applies).
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO human_labels (id, eval_result_id, label) VALUES ('l1', 'r1', 'good')"
            )
        )
        row = conn.execute(
            sa.text("SELECT eval_result_id, label, annotator FROM human_labels")
        ).fetchone()
    assert row == ("r1", "good", "human")

    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.downgrade()
    assert "human_labels" not in _tables(engine)

    engine.dispose()


def test_migration_0014_metadata():
    migration = importlib.import_module(MIGRATION_MOD)
    assert migration.revision == "0014"
    assert migration.down_revision == "0013"
