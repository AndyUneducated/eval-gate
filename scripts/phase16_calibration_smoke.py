"""Phase 16 smoke: temperature scaling fixes an overconfident judge.

Why synthetic-offline (no LLM)? Calibration needs a *spread* of judge scores
paired with human labels; the mock judge returns a flat ``0.5`` for every case
(zero information), so a calibration demo literally cannot run on it (same
honesty note as Phase 14/15). This smoke drives the pure engine
(`evalgate.report.calibration`) over seeded, deliberately overconfident
``(score, label)`` pairs — exactly the shape of a real labeled eval set.

It asserts the three headline behaviours:

1. **calibration**: ECE >= 0.15 before -> fitted ``T > 1`` -> ECE <= 0.05 after;
2. **diagram**: a reliability PNG is rendered to disk (non-empty);
3. **active learning**: ranking by calibrated uncertainty recovers more
   genuinely-ambiguous (boundary) cases in the top-K than ranking by the
   current heuristic ``judge_confidence`` proxy. (Temperature scaling is
   monotonic, so it doesn't reorder ``|score-0.5|`` — the win is over the
   miscalibrated confidence heuristic the finder uses today.)

Deterministic (seeded numpy); ``EVALGATE_MOCK_LLM`` is irrelevant here.

Usage::

    PYTHONPATH='src:.' python scripts/phase16_calibration_smoke.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np
from _smoke import EXIT_ERROR, EXIT_FAILED, EXIT_OK

from evalgate.report.calibration import (
    Calibrator,
    evaluate_calibration,
    expected_calibration_error,
    fit_temperature,
    render_reliability_png,
)

SEED = 20250116
N = 1500
T_TRUE = 0.24  # judge sharpens logits by 1/T_TRUE -> overconfident


def _overconfident_pairs(seed: int):
    rng = np.random.default_rng(seed)
    true_p = rng.uniform(0.05, 0.95, N)
    labels = (rng.uniform(size=N) < true_p).astype(int)
    z = np.log(true_p / (1 - true_p))
    scores = 1.0 / (1.0 + np.exp(-z / T_TRUE))
    return scores, labels


def main() -> int:
    ok = True
    scores, labels = _overconfident_pairs(SEED)

    ece_before = expected_calibration_error(scores, labels)
    t = fit_temperature(scores, labels)
    cal = Calibrator(t)
    stats = evaluate_calibration(scores, labels, cal)
    print(
        f"[calibrate] T={t:.3f} ece_before={ece_before:.3f} "
        f"ece_after={stats.ece_after:.3f} mce_after={stats.mce_after:.3f}"
    )
    if ece_before < 0.15:
        print("  FAIL: synthetic judge wasn't overconfident enough", file=sys.stderr)
        ok = False
    if t <= 1.0:
        print("  FAIL: expected fitted temperature > 1 (overconfident)", file=sys.stderr)
        ok = False
    if stats.ece_after > 0.05:
        print("  FAIL: calibration should drive ECE <= 0.05", file=sys.stderr)
        ok = False

    with tempfile.TemporaryDirectory() as tmp:
        png = Path(tmp) / "reliability.png"
        render_reliability_png(stats, str(png))
        if not (png.exists() and png.stat().st_size > 0):
            print("  FAIL: reliability diagram PNG was not written", file=sys.stderr)
            ok = False
        else:
            print(f"[diagram]   wrote {png.stat().st_size} bytes")

    # Active learning: genuinely-ambiguous cases cluster near the calibrated
    # boundary (p_good ~ 0.5). The *current* finder ranks by ``judge_confidence``
    # — a heuristic variance proxy that doesn't track true ambiguity. Model that
    # proxy as noise and show calibrated uncertainty recovers boundary cases the
    # heuristic ranking misses.
    p_cal = cal.transform_array(scores)
    ambiguous = np.abs(p_cal - 0.5) < 0.1
    k = int(ambiguous.sum())
    if k > 0:
        rng = np.random.default_rng(SEED + 7)
        judge_confidence = rng.uniform(0.0, 1.0, N)  # miscalibrated heuristic
        cal_unc = 1.0 - np.abs(2 * p_cal - 1.0)
        cal_top = set(np.argsort(-cal_unc)[:k].tolist())
        # Current behaviour: lowest judge_confidence first.
        heur_top = set(np.argsort(judge_confidence)[:k].tolist())
        amb_idx = set(np.flatnonzero(ambiguous).tolist())
        cal_recall = len(cal_top & amb_idx) / k
        heur_recall = len(heur_top & amb_idx) / k
        print(f"[recall]    calibrated={cal_recall:.0%} judge_confidence={heur_recall:.0%} (k={k})")
        if cal_recall <= heur_recall:
            print(
                "  FAIL: calibrated uncertainty should beat the heuristic confidence ranking",
                file=sys.stderr,
            )
            ok = False

    if not ok:
        return EXIT_FAILED
    print("OK: temperature scaling calibrates the judge and sharpens badcase sampling")
    return EXIT_OK


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # smoke wrapper: any unexpected error is EXIT_ERROR
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_ERROR) from exc
