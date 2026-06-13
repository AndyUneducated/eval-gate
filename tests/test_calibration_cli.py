"""`evalgate calibration` CLI: label -> fit -> report (+plot)."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import numpy as np
import pytest

from evalgate import cli
from evalgate.db.models import EvalResultRow, EvalRunRow, EvalSetRow


def _id() -> str:
    return uuid4().hex


@pytest.fixture
def patched_session(monkeypatch, db_session_factory):
    monkeypatch.setattr(cli, "SessionLocal", db_session_factory)
    yield db_session_factory


async def _seed(factory, scores: list[float]) -> list[str]:
    async with factory() as session:
        s = EvalSetRow(id=_id(), name="cal")
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
        ids: list[str] = []
        for sc in scores:
            rid = _id()
            session.add(
                EvalResultRow(
                    id=rid,
                    eval_run_id=run.id,
                    eval_case_id=_id(),
                    tags=[],
                    output={"text": "o"},
                    score=sc,
                    cost_usd=0.0,
                    latency_ms=1,
                )
            )
            ids.append(rid)
        await session.commit()
    return ids


def _overconfident(n: int, seed: int) -> tuple[list[float], list[int]]:
    rng = np.random.default_rng(seed)
    true_p = rng.uniform(0.05, 0.95, n)
    labels = (rng.uniform(size=n) < true_p).astype(int)
    z = np.log(true_p / (1 - true_p))
    scores = 1.0 / (1.0 + np.exp(-z / 0.35))
    return scores.tolist(), labels.tolist()


def test_label_fit_report_flow(patched_session, tmp_path, capsys):
    scores, labels = _overconfident(60, seed=3)
    ids = asyncio.run(_seed(patched_session, scores))
    capsys.readouterr()

    # label
    rc = cli.main(["calibration", "label", "--result", ids[0], "--label", "good"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["label"] == "good"
    for rid, y in zip(ids[1:], labels[1:], strict=True):
        rc = cli.main(["calibration", "label", "--result", rid, "--label", "good" if y else "bad"])
        assert rc == 0
    capsys.readouterr()

    # fit
    params = tmp_path / "params.json"
    rc = cli.main(["calibration", "fit", "--out", str(params)])
    assert rc == 0
    fit_out = json.loads(capsys.readouterr().out)
    assert params.exists()
    assert fit_out["temperature"] > 1.0
    assert fit_out["ece_after"] <= fit_out["ece_before"]

    # report + plot
    png = tmp_path / "reliability.png"
    rc = cli.main(["calibration", "report", "--params", str(params), "--plot", str(png)])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["plot"] == str(png)
    assert png.exists() and png.stat().st_size > 0
    assert set(report) >= {"temperature", "ece_before", "ece_after", "reliability_after"}


def test_label_unknown_result_returns_error(patched_session, capsys):
    rc = cli.main(["calibration", "label", "--result", "ghost", "--label", "good"])
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["error"] == "result_not_found"


def test_fit_insufficient_labels_exits_2(patched_session, tmp_path, capsys):
    ids = asyncio.run(_seed(patched_session, [0.9, 0.8, 0.7]))
    capsys.readouterr()
    for rid in ids:
        cli.main(["calibration", "label", "--result", rid, "--label", "good"])
    capsys.readouterr()
    rc = cli.main(["calibration", "fit", "--out", str(tmp_path / "p.json")])
    assert rc == 2
    assert json.loads(capsys.readouterr().out)["error"] == "insufficient_labels"
