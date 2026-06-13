"""Phase 15 orchestration: run_sequential_gate over a seeded DB + CLI smoke."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest

from evalgate import cli
from evalgate.core.schemas import EvalRecord
from evalgate.db.models import EvalResultRow, EvalRunRow
from evalgate.eval_set import repository as set_repo
from evalgate.gate import sequential as gate_seq

_PROMPT = """
name: t
candidate:
  model: ollama/qwen3.5:9b
  user_template: "{prompt}"
  params: {}
judges:
  - model: ollama/qwen3.5:9b
    rubric: "rate 0..1 strict json"
    params: {}
judge_policy:
  mode: pointwise
  k: 1
  position_swap: false
  concurrency: 2
"""


def _id() -> str:
    return uuid4().hex


async def _seed(factory, baseline_scores: list[float]) -> tuple[str, str, list[str]]:
    """Create a set with one case per score and a baseline run scoring them."""
    async with factory() as session:
        s = await set_repo.create_eval_set(session, name="seq-demo")
        case_ids: list[str] = []
        for i in range(len(baseline_scores)):
            row = await set_repo.add_case(
                session, set_id=s.id, input={"prompt": f"q{i}"}, tags=["billing"]
            )
            case_ids.append(row.id)
        set_id = s.id

    async with factory() as session:
        run = EvalRunRow(
            id=_id(),
            eval_set_id=set_id,
            prompt_path="p",
            prompt_hash="h" * 64,
            candidate_model="m",
            judge_model="j",
        )
        session.add(run)
        await session.commit()
        baseline_run_id = run.id
        for cid, score in zip(case_ids, baseline_scores, strict=True):
            session.add(
                EvalResultRow(
                    id=_id(),
                    eval_run_id=baseline_run_id,
                    eval_case_id=cid,
                    tags=["billing"],
                    output={"text": "o"},
                    score=score,
                    # Generous baseline cost/latency so the fixed-N snapshot axes
                    # never regress — these tests isolate the quality decision.
                    cost_usd=1.0,
                    latency_ms=100_000,
                )
            )
        await session.commit()
    return set_id, baseline_run_id, case_ids


def _stream(case_ids: list[str], scores: list[float]) -> AsyncIterator[EvalRecord]:
    async def gen() -> AsyncIterator[EvalRecord]:
        for cid, score in zip(case_ids, scores, strict=True):
            yield EvalRecord(
                case_id=cid, tags=["billing"], score=score, cost_usd=0.01, latency_ms=10
            )

    return gen()


async def _run(factory, set_id, baseline_run_id, stream, **kw):
    async with factory() as session:
        return await gate_seq.run_sequential_gate(
            session,
            eval_set=set_id,
            prompt_path="unused.yaml",
            baseline_run_id=baseline_run_id,
            record_stream=stream,
            **kw,
        )


def test_regression_stream_stops_early_and_fails(db_session_factory):
    base = [0.85] * 40
    set_id, baseline_run_id, case_ids = asyncio.run(_seed(db_session_factory, base))
    cand = [0.50 + 0.01 * (i % 3) for i in range(40)]  # ~ -0.35 drift, low variance
    result = asyncio.run(
        _run(db_session_factory, set_id, baseline_run_id, _stream(case_ids, cand), look_every=5)
    )
    report = result.report
    assert report.sequential is not None
    assert report.sequential.decision == "fail"
    assert report.sequential.stopped_early
    assert report.sequential.cases_consumed < report.sequential.n_max
    assert not report.passed
    quality = next(a for a in report.axes if a.name == "quality")
    assert not quality.passed


def test_clean_stream_passes(db_session_factory):
    base = [0.7 + 0.05 * ((i % 5) - 2) for i in range(60)]
    set_id, baseline_run_id, case_ids = asyncio.run(_seed(db_session_factory, base))
    cand = [0.72 + 0.05 * ((i % 5) - 2) for i in range(60)]  # ~ flat, tiny improvement
    result = asyncio.run(
        _run(db_session_factory, set_id, baseline_run_id, _stream(case_ids, cand), look_every=5)
    )
    assert result.report.sequential.decision == "pass"
    assert result.report.passed


def test_unpaired_candidate_cases_are_excluded(db_session_factory):
    """Active candidate cases without a baseline score don't advance the test."""
    base = [0.7] * 10
    set_id, baseline_run_id, case_ids = asyncio.run(_seed(db_session_factory, base))

    # Add an active case that the baseline run never scored.
    async def _add_extra():
        async with db_session_factory() as session:
            row = await set_repo.add_case(
                session, set_id=set_id, input={"prompt": "extra"}, tags=["billing"]
            )
            return row.id

    extra_id = asyncio.run(_add_extra())
    stream_ids = [*case_ids, extra_id]
    cand = [0.7] * 10 + [0.0]  # the unpaired case would tank the score if counted
    result = asyncio.run(
        _run(db_session_factory, set_id, baseline_run_id, _stream(stream_ids, cand), look_every=5)
    )
    assert result.report.sequential.n_max == 10  # only paired cases
    assert result.report.sequential.decision == "pass"


def test_missing_baseline_run_raises(db_session_factory):
    set_id, _, case_ids = asyncio.run(_seed(db_session_factory, [0.7] * 5))
    with pytest.raises(gate_seq.SequentialGateError):
        asyncio.run(_run(db_session_factory, set_id, "no-such-run", _stream(case_ids, [0.7] * 5)))


@pytest.fixture
def patched_session(monkeypatch, db_session_factory):
    monkeypatch.setattr(cli, "SessionLocal", db_session_factory)
    yield db_session_factory


def test_cli_sequential_mode(patched_session, tmp_path: Path, capsys):
    # Mock judge returns a flat 0.5; baseline flat 0.5 -> zero-variance diffs ->
    # the gate runs to exhaustion and PASSes. Exercises the CLI wiring + exit code.
    base = [0.5] * 6
    set_id, baseline_run_id, _ = asyncio.run(_seed(patched_session, base))
    capsys.readouterr()
    prompt = tmp_path / "p.yaml"
    prompt.write_text(_PROMPT)
    out = tmp_path / "report.json"
    rc = cli.main(
        [
            "run",
            "--eval-set",
            set_id,
            "--prompt",
            str(prompt),
            "--out",
            str(out),
            "--gate-mode",
            "sequential",
            "--baseline-run",
            baseline_run_id,
            "--look-every",
            "2",
            "--mock",
        ]
    )
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["passed"] is True
    report = json.loads(out.read_text())
    assert report["sequential"]["decision"] == "pass"


def test_cli_sequential_requires_baseline(patched_session, tmp_path: Path, capsys):
    set_id, _, _ = asyncio.run(_seed(patched_session, [0.5] * 3))
    capsys.readouterr()
    prompt = tmp_path / "p.yaml"
    prompt.write_text(_PROMPT)
    rc = cli.main(
        [
            "run",
            "--eval-set",
            set_id,
            "--prompt",
            str(prompt),
            "--out",
            str(tmp_path / "r.json"),
            "--gate-mode",
            "sequential",
            "--mock",
        ]
    )
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "missing_baseline"
