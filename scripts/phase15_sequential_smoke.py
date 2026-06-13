"""Phase 15 smoke: the sequential gate stops early on synthetic paired scores.

Why synthetic-offline (no LLM)? The mock judge returns a flat ``0.5`` for every
case, which is zero-variance — a statistics demo literally cannot run on it (same
honesty note as Phase 14's hit-rate). So this smoke drives the *pure* engine
(`evalgate.report.sequential.evaluate_sequential`) over seeded normal draws, which
is exactly what the candidate-vs-baseline diff stream looks like in production.

It asserts the two headline behaviours and the cost story:

1. **regressed** candidate (baseline ~ N(0.7, 0.1), candidate ~ N(0.6, 0.1)) ->
   early **FAIL**, consuming fewer than N_max cases;
2. **clean** candidate (baseline ~ N(0.7, 0.1), candidate ~ N(0.72, 0.1) — i.e.
   no regression) -> early **PASS** via curtailment, also consuming fewer than
   N_max cases;
3. both deliver >= 40% judge-call savings vs the fixed-N gate.

Deterministic (seeded numpy); ``EVALGATE_MOCK_LLM`` is irrelevant here.

Usage::

    PYTHONPATH='src:.' python scripts/phase15_sequential_smoke.py
"""

from __future__ import annotations

import sys

import numpy as np
from _smoke import EXIT_ERROR, EXIT_FAILED, EXIT_OK

from evalgate.report.sequential import evaluate_sequential

N_MAX = 60
LOOK_EVERY = 5
SEED = 20250115


def _scenario(seed: int, *, base_mu: float, cand_mu: float, sd: float = 0.1):
    rng = np.random.default_rng(seed)
    base = rng.normal(base_mu, sd, N_MAX)
    cand = rng.normal(cand_mu, sd, N_MAX)
    return evaluate_sequential(base, cand, look_every=LOOK_EVERY, mde=0.03, gamma=0.2)


def main() -> int:
    ok = True

    regressed = _scenario(SEED, base_mu=0.7, cand_mu=0.6)
    savings_r = 1.0 - regressed.cases_consumed / regressed.n_max
    print(
        f"[regressed] decision={regressed.decision} "
        f"consumed={regressed.cases_consumed}/{regressed.n_max} savings={savings_r:.0%}"
    )
    if regressed.decision != "fail":
        print("  FAIL: expected an early FAIL on an obvious regression", file=sys.stderr)
        ok = False
    if not regressed.stopped_early:
        print("  FAIL: regression should have stopped before N_max", file=sys.stderr)
        ok = False

    clean = _scenario(SEED + 1, base_mu=0.7, cand_mu=0.72)
    savings_c = 1.0 - clean.cases_consumed / clean.n_max
    print(
        f"[clean]     decision={clean.decision} "
        f"consumed={clean.cases_consumed}/{clean.n_max} savings={savings_c:.0%}"
    )
    if clean.decision != "pass":
        print("  FAIL: expected an early PASS on a clean candidate", file=sys.stderr)
        ok = False
    if not clean.stopped_early:
        print("  FAIL: clean candidate should have stopped before N_max", file=sys.stderr)
        ok = False

    if min(savings_r, savings_c) < 0.40:
        print("  FAIL: expected >= 40% judge-call savings in both scenarios", file=sys.stderr)
        ok = False

    if not ok:
        return EXIT_FAILED
    print("OK: sequential gate stops early on both regression and clean candidates")
    return EXIT_OK


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # smoke wrapper: any unexpected error is EXIT_ERROR
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR) from exc
