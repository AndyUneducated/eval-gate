"""BadCaseFinder strategy ordering — direct unit tests against aiosqlite.

We construct eval_results by hand (bypassing the Phase 6 runner) so each
strategy's ranking is exercised on a known distribution.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from evalgate.badcase import finder
from evalgate.db.models import EvalResultRow, EvalRunRow, EvalSetRow


def _new_id() -> str:
    return uuid4().hex


async def _seed_run(session) -> EvalRunRow:
    s = EvalSetRow(id=_new_id(), name="finder-test")
    session.add(s)
    await session.commit()
    run = EvalRunRow(
        id=_new_id(),
        eval_set_id=s.id,
        prompt_path="p.yaml",
        prompt_hash="h" * 64,
        candidate_model="m",
        judge_model="j",
    )
    session.add(run)
    await session.commit()
    return run


async def _add_result(
    session,
    run: EvalRunRow,
    *,
    score: float,
    confidence: float | None,
    latency_ms: int,
    cost_usd: float,
    safety: bool = False,
) -> EvalResultRow:
    axis_breakdown = None
    if safety:
        axis_breakdown = {
            "safety": {
                "pii_input_rate": 1.0,
                "pii_output_leak_rate": 0.0,
                "jailbreak_attempt_rate": 0.0,
                "jailbreak_compliance_rate": 0.0,
            }
        }
    row = EvalResultRow(
        id=_new_id(),
        eval_run_id=run.id,
        eval_case_id=None,
        tags=[],
        output={"text": f"out-{score}"},
        score=score,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        axis_breakdown=axis_breakdown,
        judge_confidence=confidence,
    )
    session.add(row)
    await session.commit()
    return row


@pytest.mark.asyncio
async def test_uncertainty_orders_by_confidence_ascending(db_session_factory):
    async with db_session_factory() as session:
        run = await _seed_run(session)
        await _add_result(session, run, score=0.9, confidence=0.95, latency_ms=10, cost_usd=0)
        await _add_result(session, run, score=0.5, confidence=0.20, latency_ms=10, cost_usd=0)
        await _add_result(session, run, score=0.7, confidence=0.60, latency_ms=10, cost_usd=0)
        # NULL confidence should sort to the *end*.
        null_row = await _add_result(
            session, run, score=0.4, confidence=None, latency_ms=10, cost_usd=0
        )

    async with db_session_factory() as session:
        cases = await finder.find_uncertainty(session, run_id=run.id, limit=10)

    assert [c.judge_confidence for c in cases[:3]] == [0.2, 0.6, 0.95]
    assert cases[-1].eval_result_id == null_row.id
    assert cases[0].reason.startswith("judge_confidence=")


@pytest.mark.asyncio
async def test_outlier_flags_zero_score_and_p95_latency(db_session_factory):
    async with db_session_factory() as session:
        run = await _seed_run(session)
        # 9 normal rows + 1 zero-score + 1 high-latency to push p95
        for _ in range(8):
            await _add_result(
                session, run, score=0.8, confidence=0.9, latency_ms=100, cost_usd=0.001
            )
        zero = await _add_result(
            session, run, score=0.0, confidence=0.9, latency_ms=110, cost_usd=0.001
        )
        slow = await _add_result(
            session, run, score=0.7, confidence=0.9, latency_ms=10_000, cost_usd=0.001
        )

    async with db_session_factory() as session:
        cases = await finder.find_outlier(session, run_id=run.id, limit=10)

    ids = {c.eval_result_id for c in cases}
    assert zero.id in ids
    assert slow.id in ids
    zero_case = next(c for c in cases if c.eval_result_id == zero.id)
    assert "score=0" in zero_case.reason
    slow_case = next(c for c in cases if c.eval_result_id == slow.id)
    assert "latency_ms=10000" in slow_case.reason


@pytest.mark.asyncio
async def test_outlier_skips_p95_when_too_few_rows(db_session_factory):
    async with db_session_factory() as session:
        run = await _seed_run(session)
        # 3 rows < MIN_FOR_PERCENTILE — only score=0 / safety should trigger.
        await _add_result(session, run, score=0.0, confidence=0.9, latency_ms=10, cost_usd=0)
        await _add_result(
            session, run, score=0.5, confidence=0.9, latency_ms=999_999, cost_usd=99.0
        )
        await _add_result(
            session, run, score=0.7, confidence=0.9, latency_ms=10, cost_usd=0, safety=True
        )

    async with db_session_factory() as session:
        cases = await finder.find_outlier(session, run_id=run.id, limit=10)

    reasons = {c.reason for c in cases}
    assert len(cases) == 2
    assert any("score=0" in r for r in reasons)
    assert any(r.startswith("safety:") for r in reasons)


@pytest.mark.asyncio
async def test_find_dispatches_by_strategy(db_session_factory):
    async with db_session_factory() as session:
        run = await _seed_run(session)
        await _add_result(session, run, score=0.5, confidence=0.1, latency_ms=10, cost_usd=0)

    async with db_session_factory() as session:
        unc = await finder.find(session, strategy="uncertainty", run_id=run.id, limit=10)
    assert len(unc) == 1
    assert unc[0].strategy == "uncertainty"

    async with db_session_factory() as session:
        with pytest.raises(ValueError):
            await finder.find(session, strategy="bogus", run_id=run.id, limit=10)


@pytest.mark.asyncio
async def test_llm_strategy_returns_flagged_only(db_session_factory):
    async with db_session_factory() as session:
        run = await _seed_run(session)
        await _add_result(session, run, score=0.5, confidence=0.1, latency_ms=10, cost_usd=0)
        await _add_result(session, run, score=0.6, confidence=0.2, latency_ms=10, cost_usd=0)

    async with db_session_factory() as session:
        cases = await finder.find_llm(
            session, run_id=run.id, limit=10, cheap_model="ollama/qwen3.5:9b", mock=True
        )

    # Mock classifier always returns subtle_bad=true -> both pass through.
    assert len(cases) == 2
    assert all(c.strategy == "llm" for c in cases)
    assert all(c.llm_label and c.llm_label.get("subtle_bad") for c in cases)
