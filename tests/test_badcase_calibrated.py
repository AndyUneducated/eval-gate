"""Phase 16: calibrated uncertainty improves BadCase recall of human-bad cases.

Constructed so the raw `judge_confidence` ranking and the calibrated-uncertainty
ranking disagree: human-bad cases sit near the calibrated decision boundary
(p_good ~ 0.5) but happen to carry *high* raw confidence, so raw uncertainty
sampling buries them while calibrated uncertainty surfaces them.
"""

from __future__ import annotations

from uuid import uuid4

from evalgate.badcase import finder as badcase_finder
from evalgate.db.models import EvalCaseRow, EvalResultRow, EvalRunRow, EvalSetRow
from evalgate.report.calibration import Calibrator


def _id() -> str:
    return uuid4().hex


async def _seed(factory, rows: list[tuple[float, float, bool]]) -> str:
    """rows = list of (score, judge_confidence, is_human_bad)."""
    async with factory() as session:
        s = EvalSetRow(id=_id(), name="bc-set")
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
        run_id = run.id
        for score, conf, _bad in rows:
            session.add(
                EvalResultRow(
                    id=_id(),
                    eval_run_id=run_id,
                    eval_case_id=_id(),
                    tags=["billing"],
                    output={"text": "o"},
                    score=score,
                    judge_confidence=conf,
                    cost_usd=0.0,
                    latency_ms=1,
                )
            )
        await session.commit()
    return run_id


async def test_calibrated_uncertainty_beats_raw_recall(db_session_factory):
    # 4 human-bad cases: ambiguous score ~0.5 (boundary) but HIGH raw confidence.
    bad = [(0.52, 0.95, True), (0.48, 0.93, True), (0.5, 0.97, True), (0.51, 0.9, True)]
    # 6 human-good cases: confident extremes but LOW raw confidence.
    good = [
        (0.98, 0.10, False),
        (0.02, 0.12, False),
        (0.97, 0.15, False),
        (0.03, 0.11, False),
        (0.99, 0.18, False),
        (0.01, 0.14, False),
    ]
    bad_scores = {r[0] for r in bad}
    run_id = await _seed(db_session_factory, bad + good)

    async with db_session_factory() as session:
        raw = await badcase_finder.find_uncertainty(session, run_id=run_id, limit=4)
    async with db_session_factory() as session:
        cal = await badcase_finder.find_uncertainty(
            session, run_id=run_id, limit=4, calibrator=Calibrator(temperature=1.0)
        )

    raw_recall = sum(1 for bc in raw if bc.score in bad_scores)
    cal_recall = sum(1 for bc in cal if bc.score in bad_scores)
    assert cal_recall == 4  # calibrated boundary surfaces all human-bad
    assert cal_recall > raw_recall  # strictly better than raw confidence
    assert cal[0].strategy == "uncertainty"
    assert "calibrated_uncertainty" in cal[0].reason


async def test_no_calibrator_keeps_raw_behavior(db_session_factory):
    run_id = await _seed(db_session_factory, [(0.5, 0.1, False), (0.5, 0.9, False)])
    async with db_session_factory() as session:
        items = await badcase_finder.find_uncertainty(session, run_id=run_id, limit=2)
    # Lowest judge_confidence first, reason references judge_confidence.
    assert items[0].judge_confidence == 0.1
    assert "judge_confidence" in items[0].reason


async def _seed_grouped(factory, rows: list[tuple[str, float]]) -> str:
    """rows = list of (task_type, score); each gets its own eval_case."""
    async with factory() as session:
        s = EvalSetRow(id=_id(), name="bc-grp")
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
            session.add(
                EvalResultRow(
                    id=_id(),
                    eval_run_id=run.id,
                    eval_case_id=cid,
                    tags=[task_type],
                    output={"text": "o"},
                    score=score,
                    cost_usd=0.0,
                    latency_ms=1,
                )
            )
        await session.commit()
        return run.id


async def test_grouped_calibrator_ranks_by_per_group_curve(db_session_factory):
    # Same raw score, different task_type curves: the high-T "agent" curve pulls
    # P(good) toward 0.5 (max uncertainty), so it must rank ahead of "rag".
    run_id = await _seed_grouped(db_session_factory, [("rag", 0.8), ("agent", 0.8)])
    cal = Calibrator(
        temperature=1.0, scope="task_type", group_temperatures={"rag": 1.0, "agent": 10.0}
    )
    async with db_session_factory() as session:
        items = await badcase_finder.find_uncertainty(
            session, run_id=run_id, limit=2, calibrator=cal
        )
    assert items[0].reason.endswith("[task_type=agent]")
    assert items[1].reason.endswith("[task_type=rag]")
