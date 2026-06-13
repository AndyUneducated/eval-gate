"""`evalgate adversarial` CLI: generate -> review -> stats."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from evalgate import cli
from evalgate.db.models import EvalSetRow
from evalgate.eval_set import repository as set_repo


def _id() -> str:
    return uuid4().hex


@pytest.fixture
def patched_session(monkeypatch, db_session_factory):
    monkeypatch.setattr(cli, "SessionLocal", db_session_factory)
    yield db_session_factory


async def _seed(factory) -> str:
    async with factory() as session:
        s = EvalSetRow(id=_id(), name="billing")
        session.add(s)
        await session.commit()
        set_id = s.id
    async with factory() as session:
        await set_repo.add_case(
            session,
            set_id=set_id,
            input={"question": "base?"},
            expected={"answer": "a"},
            tags=["billing"],
        )
    return set_id


def test_cli_generate_review_stats(patched_session, capsys):
    set_id = asyncio.run(_seed(patched_session))

    rc = cli.main(
        ["adversarial", "generate", "--set", set_id, "--tag", "billing", "--k", "4", "--mock"]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["requested"] == 4
    assert len(out["created"]) == 4
    assert all(c["status"] == "pending" for c in out["created"])
    assert all(c["source"] == "adversarial" for c in out["created"])

    # review (no decision) -> lists pending
    rc = cli.main(["adversarial", "review", "--set", set_id])
    assert rc == 0
    listing = json.loads(capsys.readouterr().out)
    assert len(listing["pending"]) == 4
    approve_id = listing["pending"][0]["id"]

    rc = cli.main(["adversarial", "review", "--set", set_id, "--approve", approve_id])
    assert rc == 0
    approved = json.loads(capsys.readouterr().out)
    assert approved["status"] == "active"

    rc = cli.main(["adversarial", "stats", "--set", set_id])
    assert rc == 0
    stats = json.loads(capsys.readouterr().out)
    assert stats["total"] == 1
    assert stats["evaluated"] == 0
    assert stats["threshold"] == 0.5


def test_cli_generate_unknown_set_returns_1(patched_session, capsys):
    asyncio.run(_seed(patched_session))
    rc = cli.main(
        ["adversarial", "generate", "--set", "ghost", "--tag", "billing", "--k", "2", "--mock"]
    )
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "eval_set_not_found"


def test_cli_review_unknown_case_returns_1(patched_session, capsys):
    set_id = asyncio.run(_seed(patched_session))
    rc = cli.main(["adversarial", "review", "--set", set_id, "--reject", "nope"])
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "case_not_found"
