"""Phase 9 smoke: seed agent-demo -> baseline/candidate run -> gate checks.

Defaults to mock (offline, deterministic). Set ``EVALGATE_MOCK_LLM=0`` to drive
the agent planner against a real Ollama model — in that mode the deterministic
"candidate must regress" assertion is relaxed to a connectivity check, since the
real model's trajectory isn't guaranteed to regress on the weakened prompt.

Usage:
    uv run python scripts/phase9_agent_smoke.py                 # mock
    EVALGATE_MOCK_LLM=0 uv run python scripts/phase9_agent_smoke.py   # real Ollama
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from _smoke import EXIT_ERROR, EXIT_FAILED, EXIT_OK, mock_from_env
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


async def _run_one(database_url: str, set_id: str, prompt_yaml: Path, *, mock: bool) -> dict:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            result = await eval_runner.run_eval(
                session,
                eval_set_id_or_name=set_id,
                prompt_path=str(prompt_yaml),
                mock=mock,
            )
        return {
            "run_id": result.run_id,
            "mean_score": result.mean_score,
            "records": [r.model_dump() for r in result.records],
        }
    finally:
        await engine.dispose()


async def _amain() -> int:
    mock = mock_from_env(default=True)
    print(f"mode={'mock' if mock else 'real (Ollama)'}")
    if not os.environ.get("DATABASE_URL"):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
    database_url = os.environ["DATABASE_URL"]

    await _ensure_schema(database_url)
    set_id = await seed(database_url)
    print(f"seeded set_id={set_id} name={SET_NAME} cases={len(CASES)}")

    baseline = await _run_one(database_url, set_id, BASELINE_YAML, mock=mock)
    candidate = await _run_one(database_url, set_id, CANDIDATE_YAML, mock=mock)

    expected_keys = {"tool_call_accuracy", "step_wise_success"}
    for label, payload in (("baseline", baseline), ("candidate", candidate)):
        for rec in payload["records"]:
            sub = (rec.get("axis_breakdown") or {}).get("quality") or {}
            if set(sub) != expected_keys:
                print(
                    f"ERROR {label}: bad axis_breakdown.quality keys {set(sub)} != {expected_keys}",
                    file=sys.stderr,
                )
                return EXIT_ERROR

    report = build_gate_report(baseline["records"], candidate["records"])
    quality = next((a for a in report.axes if a.name == "quality"), None)
    if quality is None or not quality.sub_metrics:
        print("ERROR: quality.sub_metrics missing", file=sys.stderr)
        return EXIT_ERROR

    if set(quality.sub_metrics) != expected_keys:
        print("ERROR: quality.sub_metrics keys mismatch", file=sys.stderr)
        return EXIT_ERROR

    print(json.dumps(report.model_dump(mode="json"), indent=2))

    # The deterministic "middle-step error not masked by the final answer"
    # regression is only guaranteed under the mock fixtures. Against a real model
    # the trajectory is nondeterministic, so we only require the pipeline to have
    # produced trajectory sub-metrics (checked above).
    if not mock:
        print("OK: agent planner exercised against real LM; trajectory sub-metrics present.")
        return EXIT_OK

    by_case_a = {r["case_id"]: r for r in baseline["records"]}
    for rec in candidate["records"]:
        base = by_case_a.get(rec["case_id"])
        if not base:
            continue
        rec_sub = (rec.get("axis_breakdown") or {}).get("quality") or {}
        base_sub = (base.get("axis_breakdown") or {}).get("quality") or {}
        if rec.get("score", 0.0) < base.get("score", 0.0) and (
            rec_sub.get("step_wise_success", 0.0) < base_sub.get("step_wise_success", 0.0)
        ):
            print("OK: candidate shows a step-wise regression masked by the final answer.")
            return EXIT_OK

    print("FAILED: no case showed the expected step-wise regression", file=sys.stderr)
    return EXIT_FAILED


def main() -> None:
    raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
