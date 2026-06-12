"""Phase 12 end-to-end CI gate: real judge output, no fixtures.

This is the orchestrator the ``eval-gate`` workflow runs. It replaces the old
``seed_demo.py`` + static ``examples/fixtures/*.json`` path with a real
``run -> run -> gate`` pipeline over the Phase 12 reference set:

1. seed ``examples/ci_demo`` (generic + rag + agent + safety in one set);
2. run the **baseline** prompt (``examples/ci_demo/prompts/baseline.yaml``);
3. run the **candidate** prompt (``examples/ci_demo/prompts/candidate.yaml``);
4. diff the two record sets through ``build_gate_report``.

Two modes:

- **mock** (``--mock`` or ``EVALGATE_MOCK_LLM=1``): no network, deterministic.
  Both prompts score identically over the same set, so the gate passes — this
  is the plumbing/connectivity check the CI runs on every PR (zero token cost).
- **real** (no mock; needs a local Ollama with ``qwen3.5:9b`` +
  ``qwen3-embedding:8b``): the weakened candidate prompt actually regresses, so
  the gate may fail. Used for the demo / Phase 17 numbers.

Usage::

    # mock (CI / offline)
    EVALGATE_MOCK_LLM=1 PYTHONPATH='src:.' python scripts/phase12_ci_gate.py \
        --out gate-report.json

    # real, against local Ollama
    PYTHONPATH='src:.' python scripts/phase12_ci_gate.py --out gate-report.json

Exit codes:
- ``2`` — connectivity/plumbing broken (a task type errored, or the gate report
  is missing an expected axis / sub-metric). Always fails CI hard.
- ``1`` — pipeline healthy but the gate decided FAIL (a real regression).
- ``0`` — pipeline healthy and the gate PASSED.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from _smoke import EXIT_ERROR, EXIT_FAILED, EXIT_OK, mock_from_env
from examples.ci_demo.seed import SET_NAME, TOTAL_CASES, seed
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evalgate.core.schemas import TaskKind
from evalgate.db.models import Base
from evalgate.eval_set import repository
from evalgate.evaluator import runner as eval_runner
from evalgate.gate.decision import build_gate_report

REPO = Path(__file__).resolve().parent.parent
PROMPTS = REPO / "examples" / "ci_demo" / "prompts"
BASELINE_YAML = PROMPTS / "baseline.yaml"
CANDIDATE_YAML = PROMPTS / "candidate.yaml"

# Sub-metric keys we expect the mixed set to surface in the gate report.
_RAG_KEYS = {"faithfulness", "context_precision", "answer_relevance"}
_AGENT_KEYS = {"tool_call_accuracy", "step_wise_success"}
_SAFETY_KEYS = {
    "pii_input_rate",
    "pii_output_leak_rate",
    "jailbreak_attempt_rate",
    "jailbreak_compliance_rate",
}
_REQUIRED_AXES = {"quality", "cost", "latency_p95", "safety"}


async def _ensure_schema(database_url: str) -> None:
    if not database_url.startswith("sqlite"):
        return
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _case_task_types(database_url: str, set_id: str) -> dict[str, str]:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            cases = await repository.list_cases(session, set_id)
        return {c.id: str(c.task_type) for c in cases}
    finally:
        await engine.dispose()


async def _run_one(database_url: str, set_id: str, prompt_path: Path, *, mock: bool) -> list[dict]:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            result = await eval_runner.run_eval(
                session,
                eval_set_id_or_name=set_id,
                prompt_path=str(prompt_path),
                mock=True if mock else None,
            )
        return [r.model_dump() for r in result.records]
    finally:
        await engine.dispose()


def _assert_connectivity(
    baseline: list[dict],
    candidate: list[dict],
    task_types: dict[str, str],
) -> list[str]:
    """Return a list of failure messages (empty == all good)."""
    failures: list[str] = []

    for label, records in (("baseline", baseline), ("candidate", candidate)):
        if len(records) != TOTAL_CASES:
            failures.append(f"{label}: expected {TOTAL_CASES} records, got {len(records)}")
        # Every task type must produce at least one non-error record.
        seen_ok: dict[str, bool] = {
            k: False for k in (TaskKind.generic, TaskKind.rag, TaskKind.agent)
        }
        for rec in records:
            tt = task_types.get(rec.get("case_id", ""), "")
            if tt in seen_ok and not rec.get("error", False):
                seen_ok[tt] = True
        for tt, ok in seen_ok.items():
            if not ok:
                failures.append(f"{label}: no non-error record for task_type={tt}")
    return failures


def _assert_report(report) -> list[str]:
    failures: list[str] = []
    axes = {a.name: a for a in report.axes}
    missing_axes = _REQUIRED_AXES - set(axes)
    if missing_axes:
        failures.append(f"gate report missing axes: {sorted(missing_axes)}")
        return failures

    quality = axes["quality"]
    q_subs = set(quality.sub_metrics or {})
    if not q_subs >= _RAG_KEYS:
        failures.append(f"quality.sub_metrics missing RAG keys: {sorted(_RAG_KEYS - q_subs)}")
    if not q_subs >= _AGENT_KEYS:
        failures.append(f"quality.sub_metrics missing agent keys: {sorted(_AGENT_KEYS - q_subs)}")

    safety = axes["safety"]
    s_subs = set(safety.sub_metrics or {})
    if s_subs != _SAFETY_KEYS:
        failures.append(f"safety.sub_metrics keys = {sorted(s_subs)} (want {sorted(_SAFETY_KEYS)})")
    return failures


async def _amain(args: argparse.Namespace) -> int:
    mock = bool(args.mock) or mock_from_env()

    if not os.environ.get("DATABASE_URL"):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
        print(f"using ephemeral DB: {os.environ['DATABASE_URL']}")
    database_url = os.environ["DATABASE_URL"]

    print(f"mode: {'mock' if mock else 'real (local Ollama)'}")
    await _ensure_schema(database_url)

    set_id = await seed(database_url)
    task_types = await _case_task_types(database_url, set_id)
    print(f"seeded set={SET_NAME!r} ({set_id}) cases={TOTAL_CASES}")

    t0 = time.perf_counter()
    print("running baseline prompt...")
    baseline = await _run_one(database_url, set_id, BASELINE_YAML, mock=mock)
    print("running candidate prompt...")
    candidate = await _run_one(database_url, set_id, CANDIDATE_YAML, mock=mock)
    elapsed = time.perf_counter() - t0

    failures = _assert_connectivity(baseline, candidate, task_types)

    report = build_gate_report(baseline, candidate)
    failures += _assert_report(report)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report.model_dump_json(indent=2))
        print(f"wrote gate report -> {args.out}")

    print(json.dumps(report.model_dump(mode="json"), indent=2))
    print(f"\nelapsed: {elapsed:.1f}s ({len(baseline) + len(candidate)} evals across two runs)")

    if failures:
        print("CONNECTIVITY FAILURES:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return EXIT_ERROR

    print(f"gate decision: {'PASS' if report.passed else 'FAIL'}")
    if report.summary:
        print(f"summary: {report.summary}")
    # phase12 IS the gate: exit code is the verdict (0 pass / 1 regression).
    return EXIT_OK if report.passed else EXIT_FAILED


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 12 end-to-end CI gate")
    parser.add_argument(
        "--mock",
        action="store_true",
        default=False,
        help="Force litellm mock on all calls (CI / offline). Also honoured via EVALGATE_MOCK_LLM.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write the gate report JSON to this path (for CI artifact + PR comment).",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
