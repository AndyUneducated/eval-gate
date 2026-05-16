"""Phase 9 smoke: seed agent-demo -> baseline/candidate run -> gate checks.

Default run is fully offline (mock mode).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from examples.agent_demo.seed import CASES, SET_NAME, seed
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evalgate.db.models import Base
from evalgate.evaluator import runner as eval_runner
from evalgate.gate.decision import build_gate_report

REPO = Path(__file__).resolve().parent.parent
BASELINE_YAML = REPO / "examples" / "agent_demo" / "prompts" / "agent_baseline.yaml"
CANDIDATE_YAML = REPO / "examples" / "agent_demo" / "prompts" / "agent_candidate.yaml"


async def _ensure_schema(database_url: str) -> None:
    if not database_url.startswith("sqlite"):
        return
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _run_one(database_url: str, set_id: str, prompt_yaml: Path) -> dict:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            result = await eval_runner.run_eval(
                session,
                eval_set_id_or_name=set_id,
                prompt_path=str(prompt_yaml),
                mock=True,
            )
        return {
            "run_id": result.run_id,
            "mean_score": result.mean_score,
            "records": [r.model_dump() for r in result.records],
        }
    finally:
        await engine.dispose()


async def _amain() -> int:
    if not os.environ.get("DATABASE_URL"):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    database_url = os.environ["DATABASE_URL"]

    await _ensure_schema(database_url)
    set_id = await seed(database_url)
    print(f"seeded set_id={set_id} name={SET_NAME} cases={len(CASES)}")

    baseline = await _run_one(database_url, set_id, BASELINE_YAML)
    candidate = await _run_one(database_url, set_id, CANDIDATE_YAML)

    expected_keys = {"tool_call_accuracy", "step_wise_success"}
    for label, payload in (("baseline", baseline), ("candidate", candidate)):
        for rec in payload["records"]:
            sub = rec.get("sub_metrics") or {}
            if set(sub) != expected_keys:
                print(
                    f"FAIL {label}: bad sub_metrics keys {set(sub)} != {expected_keys}",
                    file=sys.stderr,
                )
                return 2

    report = build_gate_report(baseline["records"], candidate["records"])
    quality = next((a for a in report.axes if a.name == "quality"), None)
    if quality is None or not quality.sub_metrics:
        print("FAIL: quality.sub_metrics missing", file=sys.stderr)
        return 2

    if set(quality.sub_metrics) != expected_keys:
        print("FAIL: quality.sub_metrics keys mismatch", file=sys.stderr)
        return 2

    # Exit criterion: at least one case keeps final answer but has lower
    # step-wise score in candidate (middle-step error not masked by answer).
    by_case_a = {r["case_id"]: r for r in baseline["records"]}
    found = False
    for rec in candidate["records"]:
        case_id = rec["case_id"]
        base = by_case_a.get(case_id)
        if not base:
            continue
        if rec.get("score", 0.0) < base.get("score", 0.0) and (
            (rec.get("sub_metrics") or {}).get("step_wise_success", 0.0)
            < (base.get("sub_metrics") or {}).get("step_wise_success", 0.0)
        ):
            found = True
            break
    if not found:
        print("FAIL: no case showed step-wise regression", file=sys.stderr)
        return 2

    print(json.dumps(report.model_dump(mode="json"), indent=2))
    return 0


def main() -> None:
    code = asyncio.run(_amain())
    raise SystemExit(code)


if __name__ == "__main__":
    main()
