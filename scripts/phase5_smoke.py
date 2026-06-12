"""One-shot Phase 5 smoke: seed → run baseline+candidate → gate.

Uses a throwaway sqlite file (no Postgres / Docker needed). Calls real Ollama
unless `EVALGATE_MOCK_LLM=1`. Cleans up the sqlite file at the end.

Usage:
    uv run python scripts/phase5_smoke.py            # real Ollama
    EVALGATE_MOCK_LLM=1 uv run python scripts/phase5_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from _smoke import EXIT_ERROR, EXIT_OK, mock_from_env

ROOT = Path(__file__).resolve().parents[1]
_SCRATCH = Path(tempfile.gettempdir()) / "evalgate-phase5"
DB_PATH = _SCRATCH / "smoke.db"
RUNS = _SCRATCH / "runs"

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_PATH}"


async def _seed():
    """Spin up the schema and insert a 3-case eval set."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from evalgate.db.models import Base
    from evalgate.eval_set import repository

    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    cases = [
        {"prompt": "A customer was double-charged $12.50 for invoice INV-001. What should we do?"},
        {"prompt": "Why does my bill show a $5 service fee on the second line of INV-002?"},
        {"prompt": "Refund the $30 overcharge on INV-003 and tell the customer the next steps."},
    ]
    async with factory() as session:
        s = await repository.create_eval_set(session, name="billing-smoke")
        for c in cases:
            await repository.add_case(session, set_id=s.id, input=c, tags=["billing"])
    await engine.dispose()
    return "billing-smoke"


def _run(set_name: str, prompt_path: Path, out_path: Path) -> dict:
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
    if mock_from_env():
        cmd.append("--mock")
    print(f"\n$ {' '.join(cmd)}")
    res = subprocess.run(cmd, check=False, capture_output=True, text=True, cwd=ROOT)
    if res.returncode != 0:
        print(res.stdout)
        print(res.stderr, file=sys.stderr)
        raise SystemExit(EXIT_ERROR)
    summary = json.loads(res.stdout)
    print(json.dumps(summary, indent=2))
    return summary


def _gate(baseline: Path, candidate: Path) -> int:
    cmd = [
        sys.executable,
        "-m",
        "evalgate.cli",
        "gate",
        "--baseline",
        str(baseline),
        "--candidate",
        str(candidate),
    ]
    print(f"\n$ {' '.join(cmd)}")
    res = subprocess.run(cmd, check=False, capture_output=True, text=True, cwd=ROOT)
    print(res.stdout)
    print(res.stderr, file=sys.stderr)
    return res.returncode


def main() -> int:
    print(f"mode={'mock' if mock_from_env() else 'real (Ollama)'}")
    RUNS.mkdir(parents=True, exist_ok=True)
    try:
        set_name = asyncio.run(_seed())
        baseline = RUNS / "baseline.json"
        candidate = RUNS / "candidate.json"
        base_summary = _run(set_name, ROOT / "examples/prompts/baseline.yaml", baseline)
        cand_summary = _run(set_name, ROOT / "examples/prompts/candidate.yaml", candidate)
        gate_rc = _gate(baseline, candidate)
        print(f"\nGate exit code: {gate_rc} (0 = pass, 1 = regressed)")
        # Connectivity smoke: both runs must score all 3 cases and the gate must
        # run end to end (exit 0/1, not a crash). It does not assert a regression
        # because mock baseline/candidate are identical.
        for label, summary in (("baseline", base_summary), ("candidate", cand_summary)):
            if int(summary.get("total_cases", 0)) != 3:
                print(
                    f"ERROR: {label} scored {summary.get('total_cases')} cases, want 3",
                    file=sys.stderr,
                )
                return EXIT_ERROR
        if gate_rc not in (0, 1):
            print(f"ERROR: gate did not run cleanly (rc={gate_rc})", file=sys.stderr)
            return EXIT_ERROR
        return EXIT_OK
    finally:
        shutil.rmtree(_SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
