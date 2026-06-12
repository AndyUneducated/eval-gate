"""Bootstrap-CI utilities for stochastic-eval regression testing.

The CI gate uses these to decide whether an observed delta on a given axis is a
real regression vs noise from a small / variable eval set. If the 95% CI of
``statistic(candidate) - statistic(baseline)`` does not cross zero, the change
is considered significant. The statistic is pluggable so every axis — mean
aggregated (quality / cost) and tail aggregated (latency p95) — is judged with
the *same* significance machinery rather than a special-cased threshold.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

# A vectorised statistic: takes a (n_resamples, sample_size) matrix and returns
# one value per row (axis=1). Registered by name so axis specs can refer to them
# declaratively and future axes can add their own.
Statistic = Callable[[np.ndarray], np.ndarray]


def _mean(samples: np.ndarray) -> np.ndarray:
    return samples.mean(axis=1)


def _p95(samples: np.ndarray) -> np.ndarray:
    return np.percentile(samples, 95, axis=1)


STATISTICS: dict[str, Statistic] = {"mean": _mean, "p95": _p95}


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
    statistic: str | Statistic = "mean",
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int | None = 42,
) -> BootstrapResult:
    """Return a bootstrap CI for ``statistic(candidate) - statistic(baseline)``.

    Independently resamples each array with replacement; ``significant`` is
    ``True`` iff the resulting CI does not straddle zero. ``statistic`` is either
    a key in :data:`STATISTICS` (``"mean"`` / ``"p95"``) or a callable reducing a
    ``(n_resamples, sample_size)`` matrix along ``axis=1``.
    """
    if not baseline or not candidate:
        raise ValueError("baseline and candidate must each contain at least one value")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in the open interval (0, 1)")

    stat: Statistic = STATISTICS[statistic] if isinstance(statistic, str) else statistic

    rng = np.random.default_rng(seed)
    base_arr = np.asarray(baseline, dtype=np.float64)
    cand_arr = np.asarray(candidate, dtype=np.float64)

    base_idx = rng.integers(0, len(base_arr), size=(n_resamples, len(base_arr)))
    cand_idx = rng.integers(0, len(cand_arr), size=(n_resamples, len(cand_arr)))

    diffs = stat(cand_arr[cand_idx]) - stat(base_arr[base_idx])
    alpha = (1.0 - confidence) / 2.0
    ci_low, ci_high = np.quantile(diffs, [alpha, 1.0 - alpha])
    # Point estimate uses the same statistic on the full (un-resampled) samples.
    delta = float(stat(cand_arr[None, :])[0] - stat(base_arr[None, :])[0])
    significant = bool(ci_low > 0 or ci_high < 0)
    return BootstrapResult(
        delta=delta,
        ci_low=float(ci_low),
        ci_high=float(ci_high),
        significant=significant,
    )
