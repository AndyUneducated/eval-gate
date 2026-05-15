"""`evalgate badcase` CLI: list -> read eval_result_id -> promote (Phase 7.5)."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from evalgate import cli
from evalgate.db.models import EvalResultRow, EvalRunRow, EvalSetRow
from evalgate.eval_set import repository as set_repo


def _id() -> str:
    return uuid4().hex


@pytest.fixture
def patched_session(monkeypatch, db_session_factory):
    monkeypatch.setattr(cli, "SessionLocal", db_session_factory)
    yield db_session_factory


async def _seed(factory) -> dict:
    async with factory() as session:
        src = EvalSetRow(id=_id(), name="src")
        dst = EvalSetRow(id=_id(), name="dst")
        session.add_all([src, dst])
        await session.commit()
        src_id, dst_id = src.id, dst.id

    async with factory() as session:
        case = await set_repo.add_case(
            session,
            set_id=src_id,
            task_type="generic",
            input={"prompt": "x"},
            expected={"output": "ref"},
            tags=["billing"],
        )
        case_id = case.id

    async with factory() as session:
        run = EvalRunRow(
            id=_id(),
            eval_set_id=src_id,
            prompt_path="p",
            prompt_hash="h" * 64,
            candidate_model="m",
            judge_model="j",
        )
        session.add(run)
        await session.commit()
        result = EvalResultRow(
            id=_id(),
            eval_run_id=run.id,
            eval_case_id=case_id,
            tags=["billing"],
            output={"text": "out"},
            score=0.4,
            cost_usd=0.0,
            latency_ms=10,
            judge_confidence=0.15,
        )
        session.add(result)
        await session.commit()
        return {
            "src_id": src_id,
            "dst_id": dst_id,
            "result_id": result.id,
            "case_id": case_id,
        }


def test_cli_list_then_promote_round_trip(patched_session, capsys):
    seeded = asyncio.run(_seed(patched_session))

    rc = cli.main(["badcase", "list", "--strategy", "uncertainty", "--limit", "5"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["strategy"] == "uncertainty"
    assert out["items"], "expected at least one badcase"
    top = out["items"][0]
    assert top["eval_result_id"] == seeded["result_id"]

    rc2 = cli.main(
        [
            "badcase",
            "promote",
            "--result",
            seeded["result_id"],
            "--eval-set",
            "dst",
            "--strategy",
            "uncertainty",
            "--tag",
            "interesting",
        ]
    )
    assert rc2 == 0
    promoted = json.loads(capsys.readouterr().out)
    assert promoted["eval_case_id"] == seeded["case_id"]
    assert promoted["eval_set_id"] == seeded["dst_id"]
    assert promoted["strategy"] == "uncertainty"
    assert promoted["tags"] == ["interesting"]
    assert promoted["promoted_from_result_id"] == seeded["result_id"]


def test_cli_promote_twice_returns_already_promoted(patched_session, capsys):
    seeded = asyncio.run(_seed(patched_session))
    argv = [
        "badcase",
        "promote",
        "--result",
        seeded["result_id"],
        "--eval-set",
        "dst",
    ]
    assert cli.main(argv) == 0
    capsys.readouterr()
    rc = cli.main(argv)
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "already_promoted"


def test_cli_promote_unknown_result_returns_1(patched_session, capsys):
    asyncio.run(_seed(patched_session))
    rc = cli.main(
        [
            "badcase",
            "promote",
            "--result",
            "nope",
            "--eval-set",
            "dst",
        ]
    )
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "badcase_not_found"


def test_cli_promote_unknown_target_set_returns_1(patched_session, capsys):
    seeded = asyncio.run(_seed(patched_session))
    rc = cli.main(
        [
            "badcase",
            "promote",
            "--result",
            seeded["result_id"],
            "--eval-set",
            "ghost",
        ]
    )
    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "eval_set_not_found"


def test_cli_list_with_mock_llm_strategy(patched_session, capsys):
    asyncio.run(_seed(patched_session))
    rc = cli.main(
        [
            "badcase",
            "list",
            "--strategy",
            "llm",
            "--limit",
            "3",
            "--mock",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["strategy"] == "llm"
    assert len(out["items"]) == 1
    assert out["items"][0]["llm_label"]["subtle_bad"] is True
