"""Phase 17: Cohen's kappa orchestration over the human_labels store + CLI."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest

from evalgate import cli
from evalgate.calibration import repository as cal_repo
from evalgate.db.models import EvalCaseRow, EvalResultRow, EvalRunRow, EvalSetRow


def _id() -> str:
    return uuid4().hex


async def _seed(factory, rows: list[tuple[str, float]]) -> tuple[str, list[str]]:
    """rows = (task_type, score); each result gets its own case. Returns ids."""
    result_ids: list[str] = []
    async with factory() as session:
        s = EvalSetRow(id=_id(), name="agr")
        session.add(s)
        await session.commit()
        run = EvalRunRow(
            id=_id(),
            eval_set_id=s.id,
            prompt_path="p",
            prompt_hash="h" * 64,
            candidate_model="m",
            judge_model="j",
        )
        session.add(run)
        await session.commit()
        for task_type, score in rows:
            cid = _id()
            session.add(EvalCaseRow(id=cid, task_type=task_type, input={}, tags=[]))
            rid = _id()
            session.add(
                EvalResultRow(
                    id=rid,
                    eval_run_id=run.id,
                    eval_case_id=cid,
                    tags=[task_type],
                    output={"text": "o"},
                    score=score,
                    cost_usd=0.0,
                    latency_ms=1,
                )
            )
            result_ids.append(rid)
        await session.commit()
    return run.id, result_ids


async def test_compute_agreement_perfect(db_session_factory):
    rows = [("rag", 0.9), ("rag", 0.1), ("agent", 0.8), ("agent", 0.2)]
    _, ids = await _seed(db_session_factory, rows)
    # Human agrees with the >=0.5 verdict on every row.
    verdicts = ["good", "bad", "good", "bad"]
    async with db_session_factory() as session:
        for rid, v in zip(ids, verdicts, strict=True):
            await cal_repo.add_label(session, eval_result_id=rid, label=v)
    async with db_session_factory() as session:
        report = await cal_repo.compute_agreement(session)
    assert report.n == 4
    assert report.cohen_kappa == pytest.approx(1.0)
    assert (report.tp, report.fp, report.fn, report.tn) == (2, 0, 0, 2)


async def test_compute_agreement_scope_task_type(db_session_factory):
    rows = [("rag", 0.9), ("rag", 0.1), ("agent", 0.9), ("agent", 0.6)]
    _, ids = await _seed(db_session_factory, rows)
    # rag: perfect; agent: one disagreement (score 0.6 -> judge good, human bad).
    verdicts = ["good", "bad", "good", "bad"]
    async with db_session_factory() as session:
        for rid, v in zip(ids, verdicts, strict=True):
            await cal_repo.add_label(session, eval_result_id=rid, label=v)
    async with db_session_factory() as session:
        report = await cal_repo.compute_agreement(session, scope="task_type")
    assert report.scope == "task_type"
    assert set(report.groups) == {"rag", "agent"}
    assert report.groups["rag"].cohen_kappa == pytest.approx(1.0)
    assert report.groups["agent"].cohen_kappa < 1.0


async def test_compute_agreement_no_labels_raises(db_session_factory):
    await _seed(db_session_factory, [("rag", 0.9)])
    async with db_session_factory() as session:
        with pytest.raises(cal_repo.InsufficientLabelsError):
            await cal_repo.compute_agreement(session)


def test_kappa_cli_flow(monkeypatch, db_session_factory, capsys):
    monkeypatch.setattr(cli, "SessionLocal", db_session_factory)
    rows = [("rag", 0.9), ("rag", 0.1), ("agent", 0.8), ("agent", 0.2)]
    _, ids = asyncio.run(_seed(db_session_factory, rows))
    for rid, v in zip(ids, ["good", "bad", "good", "bad"], strict=True):
        cli.main(["calibration", "label", "--result", rid, "--label", v])
    capsys.readouterr()

    rc = cli.main(["calibration", "kappa", "--scope", "task_type"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["cohen_kappa"] == pytest.approx(1.0)
    assert out["threshold"] == 0.5
    assert set(out["groups"]) == {"rag", "agent"}
