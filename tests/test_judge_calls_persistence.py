"""eval_judge_calls bulk-insert + listing.

The Phase 6 forward-compat promise is: every raw judge LLM invocation that
contributed to an EvalResultRow lands as its own row in `eval_judge_calls`,
so Phase 14 (kappa) and Phase 17 (calibration) can recompute things without
re-invoking the judge.
"""

from __future__ import annotations

import pytest

from evalgate.eval_set import repository as set_repo
from evalgate.judge import persistence
from evalgate.judge.protocol import JudgeCallRecord


@pytest.mark.asyncio
async def test_add_judge_calls_round_trip(db_session_factory):
    async with db_session_factory() as session:
        s = await set_repo.create_eval_set(session, name="calls-test")
        await set_repo.add_case(session, set_id=s.id, input={"prompt": "x"}, tags=["t"])

    async with db_session_factory() as session:
        run = await persistence.create_run(
            session,
            eval_set_id=s.id,
            prompt_path="p.yaml",
            prompt_hash="h" * 64,
            candidate_model="m",
            judge_model="j1+j2",
        )
        result = await persistence.add_result(
            session,
            run_id=run.id,
            case_id=None,
            tags=["t"],
            output_text="out",
            score=0.6,
            reason=None,
            cost_usd=0.0,
            latency_ms=10,
        )
        calls = [
            JudgeCallRecord(
                judge_model="j1",
                sub_run_index=0,
                position="A_FIRST",
                score=None,
                winner="A",
                reason="a wins",
                raw={"foo": 1},
            ),
            JudgeCallRecord(
                judge_model="j1",
                sub_run_index=0,
                position="B_FIRST",
                score=None,
                winner="B",
                reason="b wins",
                raw=None,
            ),
            JudgeCallRecord(
                judge_model="j2",
                sub_run_index=0,
                score=0.7,
                reason="pointwise call",
            ),
        ]
        inserted = await persistence.add_judge_calls(session, result_id=result.id, calls=calls)
        assert len(inserted) == 3

    async with db_session_factory() as session:
        rows = await persistence.list_judge_calls(session, result.id)
    assert len(rows) == 3
    assert {r.judge_model for r in rows} == {"j1", "j2"}
    a_first = next(r for r in rows if r.position == "A_FIRST")
    assert a_first.winner == "A"
    j2 = next(r for r in rows if r.judge_model == "j2")
    assert j2.score == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_add_judge_calls_empty_is_noop(db_session_factory):
    async with db_session_factory() as session:
        result = await persistence.add_judge_calls(session, result_id="nonexistent", calls=[])
        assert result == []
