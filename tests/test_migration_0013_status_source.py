"""Phase 14: migration 0013 (eval_cases status/source) round-trip on SQLite.

Like the other migration tests, we build a minimal pre-0013 ``eval_cases``
table, run the migration module's ``upgrade()`` / ``downgrade()`` via Alembic's
Operations, and assert the columns + the ``source='trace'`` backfill for
trace-promoted rows.
"""

from __future__ import annotations

import importlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION_MOD = "evalgate.db.migrations.versions.0013_add_case_status_source"


def _build_pre_0013_table(engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "eval_cases",
        metadata,
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("task_type", sa.String, nullable=False),
        sa.Column("source_trace_id", sa.String, nullable=True),
    )
    metadata.create_all(engine)


def _seed(engine, *, row_id: str, trace_id: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO eval_cases (id, task_type, source_trace_id) "
                "VALUES (:id, 'generic', :trace)"
            ),
            {"id": row_id, "trace": trace_id},
        )


def _columns(engine) -> set[str]:
    with engine.begin() as conn:
        rows = conn.execute(sa.text("PRAGMA table_info(eval_cases)")).fetchall()
    return {row[1] for row in rows}


def test_migration_0013_round_trip(tmp_path):
    db_path = tmp_path / "mig.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    _build_pre_0013_table(engine)
    _seed(engine, row_id="manual", trace_id=None)
    _seed(engine, row_id="from_trace", trace_id="trace-123")

    migration = importlib.import_module(MIGRATION_MOD)

    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.upgrade()

    cols = _columns(engine)
    assert {"status", "source"} <= cols

    with engine.begin() as conn:
        fetched = conn.execute(sa.text("SELECT id, status, source FROM eval_cases")).fetchall()
    rows = {r[0]: (r[1], r[2]) for r in fetched}
    # Defaults applied everywhere.
    assert rows["manual"] == ("active", "manual")
    # Trace-promoted row backfilled to source='trace'.
    assert rows["from_trace"] == ("active", "trace")

    # downgrade drops both columns.
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.downgrade()
    cols = _columns(engine)
    assert "status" not in cols
    assert "source" not in cols

    engine.dispose()


@pytest.mark.parametrize("ident", ["status", "source"])
def test_migration_0013_metadata(ident):
    migration = importlib.import_module(MIGRATION_MOD)
    assert migration.revision == "0013"
    assert migration.down_revision == "0012"
    assert ident  # both columns are exercised in the round-trip above
