"""Phase 13 end-to-end shadow-mode smoke: 1k traffic -> rolling report -> alert.

Simulates production shadow traffic *offline and deterministically* (no LLM, no
HTTP): 1000 ``(primary, candidate)`` observations are written straight to
``shadow_observations`` with the candidate's ``cost_usd`` inflated 20%. We then
run the same rollup the ``evalgate shadow rollup`` CLI / ``POST /v1/shadow/rollup``
use, asserting:

1. the rolling report has all four axes (quality / cost / latency_p95 / safety);
2. the ``cost`` axis regresses (candidate +20%) -> ``report.passed`` is False;
3. quality / latency / safety stay within tolerance (identical both sides);
4. the regression alert fires (captured via an injected alerter) and the
   persisted ``shadow_reports`` row has ``alerted=True``.

Usage::

    PYTHONPATH='src:.' python scripts/phase13_shadow_smoke.py

Exit codes:
- ``2`` — a structural expectation broke (missing axis, wrong regression,
  alert didn't fire). Always fails hard.
- ``0`` — shadow mode detected the injected cost regression and alerted.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import tempfile
from pathlib import Path

from _smoke import EXIT_ERROR, EXIT_OK
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evalgate.core.schemas import GateReport
from evalgate.db.models import Base
from evalgate.shadow import persistence, rollup

REPO = Path(__file__).resolve().parent.parent

N_TRAFFIC = 1000
COST_INFLATION = 1.20  # candidate is 20% pricier than primary
CANDIDATE_HASH = "cand-deadbeef"
PRIMARY_HASH = "prim-cafef00d"
_REQUIRED_AXES = {"quality", "cost", "latency_p95", "safety"}
_SAFETY_ZERO = {
    "pii_input_rate": 0.0,
    "pii_output_leak_rate": 0.0,
    "jailbreak_attempt_rate": 0.0,
    "jailbreak_compliance_rate": 0.0,
}
_TAGS = ["billing", "support", "account"]


async def _ensure_schema(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


def _records(rng: random.Random, idx: int) -> tuple[dict, dict, list[str]]:
    """One paired (primary, candidate) observation.

    Quality + latency + safety are identical on both sides (so they stay within
    tolerance); only cost differs (+20% on candidate) to isolate the signal.
    """
    score = round(rng.uniform(0.6, 0.9), 4)
    base_cost = round(rng.uniform(0.0015, 0.0025), 6)
    latency = rng.randint(500, 1200)
    tags = [rng.choice(_TAGS)]
    case_id = f"case-{idx}"

    def rec(cost: float) -> dict:
        return {
            "case_id": case_id,
            "tags": tags,
            "score": score,
            "cost_usd": cost,
            "latency_ms": latency,
            "axis_breakdown": {"safety": dict(_SAFETY_ZERO)},
        }

    primary = rec(base_cost)
    candidate = rec(round(base_cost * COST_INFLATION, 6))
    return primary, candidate, tags


async def _seed(database_url: str) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    rng = random.Random(1313)
    try:
        async with factory() as session:
            for i in range(N_TRAFFIC):
                primary, candidate, tags = _records(rng, i)
                await persistence.add_observation(
                    session,
                    case_id=primary["case_id"],
                    tags=tags,
                    primary_prompt_hash=PRIMARY_HASH,
                    candidate_prompt_hash=CANDIDATE_HASH,
                    primary_record=primary,
                    candidate_record=candidate,
                )
    finally:
        await engine.dispose()


def _assert_report(report: GateReport, alerts: list[GateReport], alerted: bool) -> list[str]:
    failures: list[str] = []
    axes = {a.name: a for a in report.axes}

    missing = _REQUIRED_AXES - set(axes)
    if missing:
        failures.append(f"rolling report missing axes: {sorted(missing)}")
        return failures

    cost = axes["cost"]
    if cost.passed:
        failures.append("cost axis should regress (+20% candidate) but passed")
    if not cost.significant:
        failures.append("cost regression should be statistically significant over 1k samples")
    if cost.delta <= 0:
        failures.append(f"cost delta should be positive (costlier candidate), got {cost.delta}")

    for name in ("quality", "latency_p95", "safety"):
        if not axes[name].passed:
            failures.append(f"{name} axis should stay within tolerance but regressed")

    if report.passed:
        failures.append("overall rolling report should FAIL on the cost regression")

    if not alerts:
        failures.append("regression alert should have fired")
    if not alerted:
        failures.append("persisted shadow_reports row should have alerted=True")
    return failures


async def _amain() -> int:
    if not os.environ.get("DATABASE_URL"):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
        print(f"using ephemeral DB: {os.environ['DATABASE_URL']}")
    database_url = os.environ["DATABASE_URL"]

    if database_url.startswith("sqlite"):
        await _ensure_schema(database_url)

    print(
        f"seeding {N_TRAFFIC} shadow observations (candidate cost +{(COST_INFLATION - 1) * 100:.0f}%)..."
    )
    await _seed(database_url)

    # Capture alerts in-process instead of POSTing to a live webhook.
    captured: list[GateReport] = []

    async def _recording_alerter(report: GateReport) -> bool:
        captured.append(report)
        return True

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            row = await rollup.run_rollup(
                session,
                CANDIDATE_HASH,
                window_hours=24,
                alerter=_recording_alerter,
            )
    finally:
        await engine.dispose()

    report = GateReport.model_validate(row.report)
    failures = _assert_report(report, captured, row.alerted)

    print(f"\nrolling report over n={row.n_observations} observations:")
    for axis in report.axes:
        flag = "PASS" if axis.passed else "FAIL"
        print(
            f"  [{flag}] {axis.name:<12} "
            f"baseline={axis.baseline:.4f} candidate={axis.candidate:.4f} "
            f"delta={axis.delta:+.4f} significant={axis.significant}"
        )
    print(f"\noverall: {'PASS' if report.passed else 'FAIL'}  |  alerted={row.alerted}")
    if report.summary:
        print(f"summary: {report.summary}")

    if failures:
        print("\nSMOKE FAILURES:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return EXIT_ERROR

    print("\nshadow mode detected the injected cost regression and alerted. OK.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
