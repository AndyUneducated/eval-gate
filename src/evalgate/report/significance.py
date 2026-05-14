"""Bootstrap-CI utilities for stochastic-eval regression testing.

The CI gate uses these to decide whether an observed mean delta on a given
axis is a real regression vs noise from a small / variable eval set. If the
95% CI of `mean(candidate) - mean(baseline)` does not cross zero, the change
is considered significant.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BootstrapResult:
    delta: float
    ci_low: float
    ci_high: float
    significant: bool

    @property
    def direction(self) -> str:
        if self.delta > 0:
            return "up"
        if self.delta < 0:
            return "down"
        return "flat"


def bootstrap_diff_ci(
    baseline: list[float],
    candidate: list[float],
    *,
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int | None = 42,
) -> BootstrapResult:
    """Return a bootstrap CI for `mean(candidate) - mean(baseline)`.

    Independently resamples each array with replacement; ``significant`` is
    ``True`` iff the resulting CI does not straddle zero.
    """
    if not baseline or not candidate:
        raise ValueError("baseline and candidate must each contain at least one value")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in the open interval (0, 1)")

    rng = np.random.default_rng(seed)
    base_arr = np.asarray(baseline, dtype=np.float64)
    cand_arr = np.asarray(candidate, dtype=np.float64)

    base_idx = rng.integers(0, len(base_arr), size=(n_resamples, len(base_arr)))
    cand_idx = rng.integers(0, len(cand_arr), size=(n_resamples, len(cand_arr)))

    diffs = cand_arr[cand_idx].mean(axis=1) - base_arr[base_idx].mean(axis=1)
    alpha = (1.0 - confidence) / 2.0
    ci_low, ci_high = np.quantile(diffs, [alpha, 1.0 - alpha])
    delta = float(cand_arr.mean() - base_arr.mean())
    significant = bool(ci_low > 0 or ci_high < 0)
    return BootstrapResult(
        delta=delta,
        ci_low=float(ci_low),
        ci_high=float(ci_high),
        significant=significant,
    )
