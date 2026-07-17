"""Phase 16: human-label store + fit/load orchestration."""

from __future__ import annotations

from uuid import uuid4

import numpy as np
import pytest

from evalgate.calibration import repository as cal_repo
from evalgate.db.models import EvalCaseRow, EvalResultRow, EvalRunRow, EvalSetRow


def _id() -> str:
    return uuid4().hex


async def _seed_run(factory, scores: list[float | None]) -> tuple[str, list[str]]:
    async with factory() as session:
        s = EvalSetRow(id=_id(), name="cal-set")
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
        result_ids: list[str] = []
        for sc in scores:
            rid = _id()
            session.add(
                EvalResultRow(
                    id=rid,
                    eval_run_id=run_id,
                    eval_case_id=_id(),
                    tags=["billing"],
                    output={"text": "o"},
                    score=0.0 if sc is None else sc,
                    cost_usd=0.0,
                    latency_ms=1,
                )
            )
            result_ids.append(rid)
        await session.commit()
    return run_id, result_ids


async def test_add_and_list_labels(db_session_factory):
    _, result_ids = await _seed_run(db_session_factory, [0.8, 0.2])
    async with db_session_factory() as session:
        await cal_repo.add_label(session, eval_result_id=result_ids[0], label="good")
        await cal_repo.add_label(session, eval_result_id=result_ids[1], label="bad", note="wrong")
    async with db_session_factory() as session:
        labels = await cal_repo.list_labels(session)
    assert len(labels) == 2
    assert {row.label for row in labels} == {"good", "bad"}


async def test_add_label_unknown_result_raises(db_session_factory):
    async with db_session_factory() as session:
        with pytest.raises(cal_repo.ResultNotFoundError):
            await cal_repo.add_label(session, eval_result_id="ghost", label="good")


async def test_fetch_scored_labels_maps_and_dedupes(db_session_factory):
    _, result_ids = await _seed_run(db_session_factory, [0.8, 0.3])
    async with db_session_factory() as session:
        await cal_repo.add_label(session, eval_result_id=result_ids[0], label="bad")
        # Re-label result 0 -> good; latest wins.
        await cal_repo.add_label(session, eval_result_id=result_ids[0], label="good")
        await cal_repo.add_label(session, eval_result_id=result_ids[1], label="bad")
    async with db_session_factory() as session:
        scores, labels, ids = await cal_repo.fetch_scored_labels(session)
    pairs = dict(zip(ids, zip(scores, labels, strict=True), strict=True))
    assert pairs[result_ids[0]] == (pytest.approx(0.8), 1)  # latest good
    assert pairs[result_ids[1]] == (pytest.approx(0.3), 0)


async def test_fit_and_save_then_load(db_session_factory, tmp_path):
    rng = np.random.default_rng(2)
    n = 60
    true_p = rng.uniform(0.05, 0.95, n)
    labels = (rng.uniform(size=n) < true_p).astype(int)
    z = np.log(true_p / (1 - true_p))
    scores = (1.0 / (1.0 + np.exp(-z / 0.4))).tolist()

    _, result_ids = await _seed_run(db_session_factory, scores)
    async with db_session_factory() as session:
        for rid, y in zip(result_ids, labels, strict=True):
            await cal_repo.add_label(session, eval_result_id=rid, label="good" if y else "bad")

    params = tmp_path / "calibration_params.json"
    async with db_session_factory() as session:
        report = await cal_repo.fit_and_save(session, params_path=str(params))
    assert params.exists()
    assert report.temperature > 1.0
    assert report.ece_after <= report.ece_before

    cal = cal_repo.load_calibrator(str(params))
    assert cal is not None
    assert cal.temperature == pytest.approx(report.temperature)
    # Missing file -> None.
    assert cal_repo.load_calibrator(str(tmp_path / "nope.json")) is None


async def test_fit_insufficient_labels_raises(db_session_factory):
    _, result_ids = await _seed_run(db_session_factory, [0.9, 0.8, 0.7])
    async with db_session_factory() as session:
        for rid in result_ids:
            await cal_repo.add_label(session, eval_result_id=rid, label="good")
    async with db_session_factory() as session:
        with pytest.raises(cal_repo.InsufficientLabelsError):
            await cal_repo.fit_and_save(session, params_path="unused.json")


def _overconfident(n: int, t_true: float, seed: int) -> tuple[list[float], list[int]]:
    rng = np.random.default_rng(seed)
    true_p = rng.uniform(0.05, 0.95, n)
    labels = (rng.uniform(size=n) < true_p).astype(int)
    z = np.log(true_p / (1 - true_p))
    scores = 1.0 / (1.0 + np.exp(-z / t_true))
    return scores.tolist(), labels.astype(int).tolist()


async def _seed_by_task_type(factory, spec: dict[str, tuple[list[float], list[int]]]):
    """Seed results whose eval_cases carry a task_type; return {result_id: label}."""
    labels_by_id: dict[str, int] = {}
    async with factory() as session:
        s = EvalSetRow(id=_id(), name="cal-set")
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
        for task_type, (scores, labels) in spec.items():
            for sc, y in zip(scores, labels, strict=True):
                case_id = _id()
                session.add(
                    EvalCaseRow(id=case_id, task_type=task_type, input={}, tags=[task_type])
                )
                rid = _id()
                session.add(
                    EvalResultRow(
                        id=rid,
                        eval_run_id=run.id,
                        eval_case_id=case_id,
                        tags=[task_type],
                        output={"text": "o"},
                        score=sc,
                        cost_usd=0.0,
                        latency_ms=1,
                    )
                )
                labels_by_id[rid] = int(y)
        await session.commit()
    return labels_by_id


async def test_fit_and_save_per_task_type(db_session_factory, tmp_path):
    rag = _overconfident(60, t_true=0.4, seed=10)
    agent = _overconfident(60, t_true=0.3, seed=11)
    labels_by_id = await _seed_by_task_type(db_session_factory, {"rag": rag, "agent": agent})

    async with db_session_factory() as session:
        for rid, y in labels_by_id.items():
            await cal_repo.add_label(session, eval_result_id=rid, label="good" if y else "bad")

    params = tmp_path / "params.json"
    async with db_session_factory() as session:
        report = await cal_repo.fit_and_save(session, params_path=str(params), scope="task_type")

    assert report.scope == "task_type"
    assert set(report.groups) == {"rag", "agent"}
    assert all(g.temperature > 1.0 for g in report.groups.values())

    cal = cal_repo.load_calibrator(str(params))
    assert cal is not None
    assert cal.scope == "task_type"
    assert set(cal.group_temperatures) == {"rag", "agent"}
    # Read-time selection uses the per-group curve; unseen group -> global T.
    assert cal.temperature_for("rag") == pytest.approx(report.groups["rag"].temperature)
    assert cal.temperature_for("unseen") == pytest.approx(report.temperature)


async def test_group_keys_for_rows_task_type_and_judge_model(db_session_factory):
    labels_by_id = await _seed_by_task_type(
        db_session_factory,
        {"rag": _overconfident(10, 0.4, 1), "agent": _overconfident(10, 0.4, 2)},
    )
    result_ids = list(labels_by_id)
    async with db_session_factory() as session:
        by_task = await cal_repo.fetch_group_keys(session, result_ids, scope="task_type")
        by_judge = await cal_repo.fetch_group_keys(session, result_ids, scope="judge_model")
        by_global = await cal_repo.fetch_group_keys(session, result_ids, scope="global")
    assert set(by_task.values()) == {"rag", "agent"}
    assert set(by_judge.values()) == {"j"}  # single run seeded with judge_model="j"
    assert by_global == {}  # global scope short-circuits
