"""Seed eval runs that exercise the Reports gate UI (pass + several fail shapes).

Creates eval set ``gate-demo`` with 20 generic cases and six runs:

* ``demo/baseline-good`` — all axes healthy (use as baseline in the UI)
* ``demo/candidate-all-fail`` — quality / cost / latency_p95 / safety all regress
* ``demo/candidate-quality-only`` — score drop + RAG sub-metrics
* ``demo/candidate-safety-only`` — safety violations + safety sub-rates
* ``demo/candidate-cost-only`` — cost spike only
* ``demo/candidate-latency-only`` — tail latency spike only

Usage::

    make db-up
    uv run alembic upgrade head
    export DATABASE_URL=postgresql+asyncpg://evalgate:evalgate@localhost:5433/evalgate
    uv run python scripts/seed_gate_demo.py

Then in the UI **Reports** tab: eval set **gate-demo**, pick ``baseline-good`` vs any
``candidate-*`` run, click **Run gate**.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evalgate.core.schemas import TaskKind
from evalgate.eval_set import repository as set_repo
from evalgate.judge import persistence as run_repo

SET_NAME = "gate-demo"
N_CASES = 20
TAGS = ("billing", "qa", "general", "safety")

# RAG-style quality breakdown (drives sub-metrics under the quality axis).
_QUALITY_GOOD = {
    "faithfulness": 0.92,
    "answer_relevancy": 0.90,
    "context_precision": 0.88,
}
_QUALITY_BAD = {
    "faithfulness": 0.35,
    "answer_relevancy": 0.40,
    "context_precision": 0.38,
}

# Safety breakdown (drives sub-metrics under the safety axis).
_SAFETY_GOOD = {
    "pii_input_rate": 0.0,
    "pii_output_leak_rate": 0.0,
    "jailbreak_attempt_rate": 0.05,
    "jailbreak_compliance_rate": 0.0,
}
_SAFETY_BAD = {
    "pii_input_rate": 0.35,
    "pii_output_leak_rate": 0.25,
    "jailbreak_attempt_rate": 0.40,
    "jailbreak_compliance_rate": 0.30,
}


def _tag(i: int) -> list[str]:
    return [TAGS[i % len(TAGS)]]


def _baseline_record(case_id: str, i: int) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "tags": _tag(i),
        "score": 0.90,
        "cost_usd": 0.008,
        "latency_ms": 700 + (i % 5) * 20,
        "safety_violation": False,
        "axis_breakdown": {
            "quality": dict(_QUALITY_GOOD),
            "safety": dict(_SAFETY_GOOD),
        },
    }


def _candidate_all_fail(case_id: str, i: int) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "tags": _tag(i),
        "score": 0.48,
        "cost_usd": 0.045,
        "latency_ms": 4500 + (i % 3) * 200,
        "safety_violation": i % 5 < 2,  # 40% violation rate
        "axis_breakdown": {
            "quality": dict(_QUALITY_BAD),
            "safety": dict(_SAFETY_BAD),
        },
    }


def _candidate_quality_only(case_id: str, i: int) -> dict[str, Any]:
    base = _baseline_record(case_id, i)
    base["score"] = 0.45
    base["axis_breakdown"] = {"quality": dict(_QUALITY_BAD), "safety": dict(_SAFETY_GOOD)}
    return base


def _candidate_safety_only(case_id: str, i: int) -> dict[str, Any]:
    base = _baseline_record(case_id, i)
    base["safety_violation"] = i % 4 == 0
    base["axis_breakdown"] = {"quality": dict(_QUALITY_GOOD), "safety": dict(_SAFETY_BAD)}
    return base


def _candidate_cost_only(case_id: str, i: int) -> dict[str, Any]:
    base = _baseline_record(case_id, i)
    base["cost_usd"] = 0.06
    return base


def _candidate_latency_only(case_id: str, i: int) -> dict[str, Any]:
    base = _baseline_record(case_id, i)
    # Spread so p95 jumps while mean stays moderate on a few fast cases.
    base["latency_ms"] = 8000 if i >= 18 else 750 + i * 10
    return base


async def _persist_run(
    session,
    *,
    eval_set_id: str,
    prompt_path: str,
    records: list[dict[str, Any]],
) -> str:
    run = await run_repo.create_run(
        session,
        eval_set_id=eval_set_id,
        prompt_path=prompt_path,
        prompt_hash=prompt_path.replace("/", "-"),
        candidate_model="demo/mock",
        judge_model="demo/mock",
    )
    scores: list[float] = []
    for rec in records:
        await run_repo.add_result(
            session,
            run_id=run.id,
            case_id=rec["case_id"],
            tags=rec["tags"],
            output_text="demo",
            score=float(rec["score"]),
            reason="seed_gate_demo",
            cost_usd=float(rec["cost_usd"]),
            latency_ms=int(rec["latency_ms"]),
            safety_violation=bool(rec["safety_violation"]),
            axis_breakdown=rec.get("axis_breakdown"),
        )
        scores.append(float(rec["score"]))
    mean = sum(scores) / len(scores) if scores else None
    await run_repo.finalize_run(session, run.id, total_cases=len(records), mean_score=mean)
    return run.id


async def seed(database_url: str) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        eval_set = await set_repo.create_eval_set(
            session,
            name=SET_NAME,
            description="Gate UI demo — multiple baseline/candidate failure shapes.",
        )
        case_ids: list[str] = []
        for i in range(N_CASES):
            case = await set_repo.add_case(
                session,
                set_id=eval_set.id,
                task_type=TaskKind.generic,
                input={"prompt": f"gate-demo question {i}"},
                expected={"answer": f"gold {i}"},
                tags=_tag(i),
            )
            case_ids.append(case.id)

        baseline_records = [_baseline_record(cid, i) for i, cid in enumerate(case_ids)]

        scenarios = [
            ("demo/baseline-good", baseline_records),
            (
                "demo/candidate-all-fail",
                [_candidate_all_fail(cid, i) for i, cid in enumerate(case_ids)],
            ),
            (
                "demo/candidate-quality-only",
                [_candidate_quality_only(cid, i) for i, cid in enumerate(case_ids)],
            ),
            (
                "demo/candidate-safety-only",
                [_candidate_safety_only(cid, i) for i, cid in enumerate(case_ids)],
            ),
            (
                "demo/candidate-cost-only",
                [_candidate_cost_only(cid, i) for i, cid in enumerate(case_ids)],
            ),
            (
                "demo/candidate-latency-only",
                [_candidate_latency_only(cid, i) for i, cid in enumerate(case_ids)],
            ),
        ]

        print(f"eval_set={SET_NAME} id={eval_set.id} cases={N_CASES}")
        for path, records in scenarios:
            run_id = await _persist_run(
                session,
                eval_set_id=eval_set.id,
                prompt_path=path,
                records=records,
            )
            print(f"  run {path} -> {run_id}")

    await engine.dispose()


def main() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    asyncio.run(seed(url))


if __name__ == "__main__":
    main()
