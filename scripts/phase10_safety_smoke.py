"""Phase 10 end-to-end smoke: seed safety demo → runner → gate.

Two eval sets get created (a clean baseline + a mixed candidate that drifts
to include PII + jailbreak inputs). We run the same candidate prompt against
each set and feed the records into ``build_gate_report`` — the safety axis
should regress on the candidate side, with at least
``pii_input_rate`` / ``jailbreak_attempt_rate`` showing a positive delta.

Usage::

    EVALGATE_MOCK_LLM=1 PYTHONPATH='src:.' python scripts/phase10_safety_smoke.py

    # Or against real Ollama (no mock); shows pii_output_leak_rate +
    # jailbreak_compliance_rate regressions when the candidate prompt is
    # weak enough to comply.
    DATABASE_URL=postgresql+asyncpg://evalgate@localhost/evalgate \
        PYTHONPATH='src:.' python scripts/phase10_safety_smoke.py

The script exits non-zero on any of:
- a record missing ``axis_breakdown.safety`` with the four sub-metrics
- gate report safety axis missing nested ``sub_metrics``
- candidate's safety main axis NOT regressing
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from examples.safety_demo.seed import BASELINE_SET_NAME, CASES, SET_NAME, seed
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evalgate.db.models import Base
from evalgate.evaluator import runner as eval_runner
from evalgate.gate.decision import build_gate_report

REPO = Path(__file__).resolve().parent.parent
PROMPT_YAML = REPO / "examples" / "safety_demo" / "prompts" / "safety_candidate.yaml"


async def _ensure_schema(database_url: str) -> None:
    if not database_url.startswith("sqlite"):
        return
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _run_one(database_url: str, set_id: str) -> dict:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            result = await eval_runner.run_eval(
                session,
                eval_set_id_or_name=set_id,
                prompt_path=str(PROMPT_YAML),
                mock=None,
            )
        return {
            "run_id": result.run_id,
            "mean_score": result.mean_score,
            "records": [r.model_dump() for r in result.records],
        }
    finally:
        await engine.dispose()


_EXPECTED_KEYS = {
    "pii_input_rate",
    "pii_output_leak_rate",
    "jailbreak_attempt_rate",
    "jailbreak_compliance_rate",
}


async def _amain() -> int:
    if not os.environ.get("DATABASE_URL"):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
        print(f"using ephemeral DB: {os.environ['DATABASE_URL']}")
    database_url = os.environ["DATABASE_URL"]

    await _ensure_schema(database_url)

    baseline_set_id, candidate_set_id = await seed(database_url)
    print(
        f"seeded baseline={BASELINE_SET_NAME!r} ({baseline_set_id}) "
        f"+ candidate={SET_NAME!r} ({candidate_set_id}) total_cases={len(CASES)}"
    )

    print("running baseline (clean inputs)...")
    baseline = await _run_one(database_url, baseline_set_id)
    print(f"  mean_score={baseline['mean_score']:.3f}")

    print("running candidate (mixed inputs incl. PII + jailbreak)...")
    candidate = await _run_one(database_url, candidate_set_id)
    print(f"  mean_score={candidate['mean_score']:.3f}")

    for label, payload in (("baseline", baseline), ("candidate", candidate)):
        for rec in payload["records"]:
            sm = (rec.get("axis_breakdown") or {}).get("safety") or {}
            if set(sm) != _EXPECTED_KEYS:
                print(
                    f"FAIL: {label} record {rec.get('case_id')} "
                    f"safety keys={set(sm)} (want {_EXPECTED_KEYS})",
                    file=sys.stderr,
                )
                return 2

    report = build_gate_report(baseline["records"], candidate["records"])
    print(json.dumps(report.model_dump(mode="json"), indent=2))

    safety = next((a for a in report.axes if a.name == "safety"), None)
    if safety is None or not safety.sub_metrics:
        print("FAIL: gate report missing safety.sub_metrics", file=sys.stderr)
        return 2
    if set(safety.sub_metrics) != _EXPECTED_KEYS:
        print(
            f"FAIL: safety.sub_metrics keys = {set(safety.sub_metrics)} (want {_EXPECTED_KEYS})",
            file=sys.stderr,
        )
        return 2

    if safety.delta <= 0:
        print(
            f"FAIL: safety axis did not regress on the candidate (delta={safety.delta:+.3f})",
            file=sys.stderr,
        )
        return 2

    if safety.passed:
        print(
            "FAIL: safety axis still passed despite a positive delta — "
            "bootstrap CI thresholds may need tuning",
            file=sys.stderr,
        )
        return 2

    print(
        f"OK: safety axis fails on candidate "
        f"(delta={safety.delta:+.3f}, sub-axes regressed: "
        f"{[k for k, v in safety.sub_metrics.items() if not v.passed]})"
    )
    return 0


def main() -> None:
    code = asyncio.run(_amain())
    sys.exit(code)


if __name__ == "__main__":
    main()
