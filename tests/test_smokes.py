"""Run every phase smoke in mock mode so their assertions execute in CI.

The smokes are the only place several end-to-end invariants are checked (badcase
promotion, RAG/agent/safety sub-axis wiring, shadow rollup + alerting, the CI
gate plumbing). Before this module they only ran by hand, so a stale assertion
could rot silently (as the Phase 10 safety check did). Here each smoke is run as
a subprocess with ``EVALGATE_MOCK_LLM=1`` and must exit 0 (``_smoke.EXIT_OK``):
no Ollama, no Postgres, deterministic.

Real-LM behaviour is exercised separately (``EVALGATE_MOCK_LLM=0``) and is not
part of the offline CI suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

# Per-smoke extra env (on top of the mock defaults). Phase 6 runs N CLI
# subprocesses per config, so pin N=1 to keep the offline run fast.
_SMOKES: dict[str, dict[str, str]] = {
    "phase5_smoke.py": {},
    "phase6_variance.py": {"N": "1"},
    "phase7_badcase_smoke.py": {},
    "phase8_rag_smoke.py": {},
    "phase9_agent_smoke.py": {},
    "phase10_safety_smoke.py": {},
    "phase12_ci_gate.py": {},
    "phase13_shadow_smoke.py": {},
}


def _env(extra: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env["EVALGATE_MOCK_LLM"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT)])
    # Force the scripts onto their ephemeral sqlite instead of any inherited DSN.
    env.pop("DATABASE_URL", None)
    env.update(extra)
    return env


@pytest.mark.parametrize("script", sorted(_SMOKES))
def test_smoke_exits_ok_in_mock(script: str) -> None:
    extra = _SMOKES[script]
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script)],
        cwd=ROOT,
        env=_env(extra),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"{script} exited {proc.returncode} in mock mode\n"
            f"--- stdout ---\n{proc.stdout[-4000:]}\n"
            f"--- stderr ---\n{proc.stderr[-4000:]}"
        )
