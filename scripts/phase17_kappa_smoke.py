"""Phase 17 smoke: Cohen's kappa (judge vs human) + p95 + conditional calibration.

Why synthetic-offline (no LLM)? Same honesty note as Phase 16: agreement needs a
*spread* of judge verdicts paired with human labels, and the mock judge returns a
flat 0.5 for every case (zero information). This smoke drives the three pure
Phase 17 engines over seeded data — exactly the shape of a real labeled set.

It asserts the three headline Phase 17 behaviours:

1. **agreement**: a judge that tracks a noisy human recovers a high Cohen's
   kappa (~the design-doc 0.85 target), with a bootstrap CI safely above chance;
2. **p95 significance**: the smoothed + sample-size-guarded bootstrap flags a
   real tail-latency regression on enough data, and *refuses* to call a thin
   sample significant (no false-block);
3. **conditional calibration**: fitting one temperature per task_type drives ECE
   lower than a single global curve when the two slices are differently
   overconfident.

Deterministic (seeded numpy); ``EVALGATE_MOCK_LLM`` is irrelevant here.

Usage::

    PYTHONPATH='src:.' python scripts/phase17_kappa_smoke.py
"""

from __future__ import annotations

import sys

import numpy as np
from _smoke import EXIT_ERROR, EXIT_FAILED, EXIT_OK

from evalgate.report.agreement import evaluate_agreement
from evalgate.report.calibration import Calibrator, evaluate_calibration, fit_temperature
from evalgate.report.significance import bootstrap_diff_ci

SEED = 20260716


def _overconfident(n: int, t_true: float, rng: np.random.Generator):
    true_p = rng.uniform(0.05, 0.95, n)
    labels = (rng.uniform(size=n) < true_p).astype(int)
    z = np.log(true_p / (1 - true_p))
    scores = 1.0 / (1.0 + np.exp(-z / t_true))
    return scores, labels


def _check_agreement(rng: np.random.Generator) -> bool:
    ok = True
    n = 600
    human = rng.integers(0, 2, n)
    # Judge agrees ~90% of the time -> strong-but-imperfect agreement.
    flip = rng.uniform(size=n) < 0.10
    judge_bit = np.where(flip, 1 - human, human)
    scores = np.where(judge_bit == 1, 0.85, 0.15)
    stats = evaluate_agreement(scores.tolist(), human.tolist(), seed=SEED)
    print(
        f"[kappa]     k={stats.cohen_kappa:.3f} "
        f"CI=[{stats.ci_low:.3f}, {stats.ci_high:.3f}] "
        f"obs={stats.observed_agreement:.3f} n={stats.n}"
    )
    if not (0.7 <= stats.cohen_kappa <= 0.9):
        print("  FAIL: expected kappa in the strong-agreement band ~0.8", file=sys.stderr)
        ok = False
    if stats.ci_low <= 0.0:
        print("  FAIL: bootstrap CI should sit above chance (0)", file=sys.stderr)
        ok = False
    return ok


def _check_p95(rng: np.random.Generator) -> bool:
    ok = True
    base = rng.normal(1000, 80, 60)
    cand = base * 1.4  # 40% slower tail
    hit = bootstrap_diff_ci(
        base.tolist(), cand.tolist(), statistic="p95", smooth=True, min_reliable_n=20, seed=SEED
    )
    thin = bootstrap_diff_ci(
        [100.0] * 8, [100.0] * 7 + [9000.0], statistic="p95", smooth=True, min_reliable_n=20
    )
    print(
        f"[p95]       regression: sig={hit.significant} reliable={hit.reliable} "
        f"delta={hit.delta:.0f} | thin-sample: sig={thin.significant} reliable={thin.reliable}"
    )
    if not (hit.significant and hit.reliable):
        print("  FAIL: a real 40% tail regression on n=60 should be significant", file=sys.stderr)
        ok = False
    if thin.significant or thin.reliable:
        print(
            "  FAIL: an 8-sample p95 must be flagged unreliable (no false-block)", file=sys.stderr
        )
        ok = False
    return ok


def _check_conditional_calibration(rng: np.random.Generator) -> bool:
    ok = True
    rag_s, rag_y = _overconfident(400, 0.4, rng)
    agt_s, agt_y = _overconfident(400, 0.25, rng)
    scores = np.concatenate([rag_s, agt_s])
    labels = np.concatenate([rag_y, agt_y])
    groups = ["rag"] * len(rag_s) + ["agent"] * len(agt_s)

    global_t = fit_temperature(scores.tolist(), labels.tolist())
    global_stats = evaluate_calibration(scores.tolist(), labels.tolist(), Calibrator(global_t))

    per_group = {
        "rag": fit_temperature(rag_s.tolist(), rag_y.tolist()),
        "agent": fit_temperature(agt_s.tolist(), agt_y.tolist()),
    }
    cond = Calibrator(temperature=global_t, scope="task_type", group_temperatures=per_group)
    cond_stats = evaluate_calibration(scores.tolist(), labels.tolist(), cond, groups=groups)
    print(
        f"[cond-cal]  global_T={global_t:.2f} ece_after={global_stats.ece_after:.3f} | "
        f"per-group T={per_group['rag']:.2f}/{per_group['agent']:.2f} "
        f"ece_after={cond_stats.ece_after:.3f}"
    )
    if cond_stats.ece_after > global_stats.ece_after + 1e-6:
        print("  FAIL: conditional calibration should not be worse than global", file=sys.stderr)
        ok = False
    return ok


def main() -> int:
    rng = np.random.default_rng(SEED)
    ok = _check_agreement(rng)
    ok = _check_p95(rng) and ok
    ok = _check_conditional_calibration(rng) and ok
    if not ok:
        return EXIT_FAILED
    print("OK: kappa agreement, guarded p95 significance, and conditional calibration all hold")
    return EXIT_OK


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # smoke wrapper: any unexpected error is EXIT_ERROR
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR) from exc
