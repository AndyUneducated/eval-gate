"""Phase 6 variance experiment.

Goal: show that a multi-judge + position-swap + self-consistency stack has
LOWER case-wise score variance across repeated runs than a single pointwise
judge. This is the headline claim Phase 6 needs to back with numbers.

Method:
    1. Seed a 5-case billing eval set (each case carries a reference
       `expected.output` so pairwise mode has something to compare against).
    2. Run BOTH configs N times:
         - single_pointwise.yaml  (1 judge,   K=1)
         - multi_pairwise.yaml    (2 judges,  K=3, position swap on)
    3. For each case, compute the stdev of its score across the N runs.
    4. Average those per-case stdevs -> one number per config.
    5. Print a markdown table; lower is better.

Uses a throwaway sqlite file (no Postgres needed). Real Ollama by default;
`EVALGATE_MOCK_LLM=1` forces mock mode (only useful to validate plumbing —
mock scores are constant so both configs show stdev 0).

Usage:
    uv run python scripts/phase6_variance.py            # real Ollama, N=3
    N=5 uv run python scripts/phase6_variance.py        # bump repetitions
    EVALGATE_MOCK_LLM=1 uv run python scripts/phase6_variance.py
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / ".phase6-variance.db"
RUNS = ROOT / ".phase6-runs"

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_PATH}"

CASES = [
    {
        "input": {
            "prompt": "A customer was double-charged $12.50 for invoice INV-001. What should we do?"
        },
        "expected": {
            "output": "Refund the $12.50 duplicate charge on INV-001, apologise, and confirm the corrected balance to the customer."
        },
    },
    {
        "input": {
            "prompt": "Why does my bill show a $5 service fee on the second line of INV-002?"
        },
        "expected": {
            "output": "The $5 fee on INV-002 line 2 is the monthly account service charge; itemise it on the next statement and offer to waive it if it's the customer's first occurrence."
        },
    },
    {
        "input": {
            "prompt": "Refund the $30 overcharge on INV-003 and tell the customer the next steps."
        },
        "expected": {
            "output": "Issue a $30 refund to the original payment method on INV-003, email the customer confirmation, and note the case ID for audit."
        },
    },
    {
        "input": {
            "prompt": "Customer claims they were charged twice on the same day for INV-004 but only see one transaction in their statement."
        },
        "expected": {
            "output": "Pull the merchant-side transaction log for INV-004, share the matching authorisation IDs with the customer, and explain that one of the two attempts is a pre-auth hold that will drop off in 5-7 days."
        },
    },
    {
        "input": {"prompt": "How do I dispute the $99 setup fee on INV-005?"},
        "expected": {
            "output": "Open a dispute ticket referencing INV-005, attach the original contract showing the setup-fee term, and tell the customer the dispute team will reply within 3 business days."
        },
    },
]


async def _seed() -> str:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from evalgate.db.models import Base
    from evalgate.eval_set import repository

    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        s = await repository.create_eval_set(session, name="billing-variance")
        for c in CASES:
            await repository.add_case(
                session,
                set_id=s.id,
                input=c["input"],
                expected=c["expected"],
                tags=["billing"],
            )
    await engine.dispose()
    return "billing-variance"


def _run_once(set_name: str, prompt_path: Path, out_path: Path) -> list[dict]:
    cmd = [
        sys.executable,
        "-m",
        "evalgate.cli",
        "run",
        "--eval-set",
        set_name,
        "--prompt",
        str(prompt_path),
        "--out",
        str(out_path),
    ]
    if os.environ.get("EVALGATE_MOCK_LLM"):
        cmd.append("--mock")
    res = subprocess.run(cmd, check=False, capture_output=True, text=True, cwd=ROOT)
    if res.returncode != 0:
        print(res.stdout)
        print(res.stderr, file=sys.stderr)
        raise SystemExit(f"run failed: {prompt_path.name}")
    return json.loads(out_path.read_text())["records"]


def _case_stdev_mean(per_run_records: list[list[dict]]) -> float:
    """Group scores by case_id across runs, compute stdev per case, average."""
    by_case: dict[str, list[float]] = {}
    for run in per_run_records:
        for rec in run:
            by_case.setdefault(rec["case_id"], []).append(float(rec["score"]))
    stdevs = [statistics.pstdev(scores) for scores in by_case.values() if len(scores) > 1]
    if not stdevs:
        return 0.0
    return sum(stdevs) / len(stdevs)


def main() -> int:
    RUNS.mkdir(exist_ok=True)
    n = int(os.environ.get("N", "3"))
    try:
        set_name = asyncio.run(_seed())

        configs = {
            "single_pointwise": ROOT / "examples/prompts/single_pointwise.yaml",
            "multi_pairwise": ROOT / "examples/prompts/multi_pairwise.yaml",
        }

        results: dict[str, list[list[dict]]] = {}
        for label, yaml_path in configs.items():
            print(f"\n=== {label}: {n} runs ===")
            runs: list[list[dict]] = []
            for i in range(n):
                out = RUNS / f"{label}_run{i}.json"
                print(f"  run {i + 1}/{n} -> {out.name}")
                runs.append(_run_once(set_name, yaml_path, out))
            results[label] = runs

        print("\n## Phase 6 variance report\n")
        print(f"Cases: {len(CASES)}; Runs per config: {n}\n")
        print("| Config | Mean per-case score stdev (lower = more stable) |")
        print("|---|---|")
        for label, runs in results.items():
            mean_std = _case_stdev_mean(runs)
            print(f"| {label} | {mean_std:.4f} |")

        return 0
    finally:
        if DB_PATH.exists():
            DB_PATH.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
