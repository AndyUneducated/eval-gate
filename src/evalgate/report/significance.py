"""Bootstrap-CI utilities for stochastic-eval regression testing.

The CI gate uses these to decide whether an observed delta on a given axis is a
real regression vs noise from a small / variable eval set. If the 95% CI of
``statistic(candidate) - statistic(baseline)`` does not cross zero, the change
is considered significant. The statistic is pluggable so every axis — mean
aggregated (quality / cost) and tail aggregated (latency p95) — is judged with
the *same* significance machinery rather than a special-cased threshold.

Tail quantiles (p95) get two extra safeguards, added in Phase 17 to retire the
"p95 v1 used a threshold, resampled-p95 interpretation is subtle" debt from
ADR-004:

- **Smoothed bootstrap** (``smooth=True``): a plain nonparametric bootstrap of a
  high quantile only ever reshuffles the same 1-2 extreme order statistics, so
  its CI is lumpy / discrete and under-covers. Adding a small kernel jitter
  (bandwidth ~ Silverman) turns the discrete empirical CDF into a smooth one and
  gives the tail quantile a stable, better-covered CI.
- **Reliability guard** (``min_reliable_n``): below a handful of samples a p95 is
  essentially the maximum — no CI is trustworthy there. When the smaller sample
  is under ``min_reliable_n`` we mark the result ``reliable=False`` and refuse to
  call it significant, so a thin-tailed axis can never *false-block* a PR (the
  exact failure mode ADR-004 exists to prevent).
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
    # Phase 17: False when the (smaller) sample is too small for the chosen
    # statistic's CI to be trustworthy — a guard against false-blocking on a
    # degenerate tail. ``significant`` is forced False whenever ``reliable`` is.
    reliable: bool = True
    n_effective: int = 0

    @property
    def direction(self) -> str:
        if self.delta > 0:
            return "up"
        if self.delta < 0:
            return "down"
        return "flat"


def _silverman_bandwidth(arr: np.ndarray) -> float:
    """Silverman's rule-of-thumb bandwidth for a smoothed bootstrap kernel."""
    n = len(arr)
    if n < 2:
        return 0.0
    sigma = float(arr.std(ddof=1))
    if sigma == 0.0:
        return 0.0
    return 0.9 * sigma * n ** (-1.0 / 5.0)


def _resample(
    arr: np.ndarray,
    idx: np.ndarray,
    rng: np.random.Generator,
    *,
    smooth: bool,
) -> np.ndarray:
    """Index ``arr`` by ``idx`` (a resample matrix), optionally kernel-smoothed."""
    drawn = arr[idx]
    if not smooth:
        return drawn
    bw = _silverman_bandwidth(arr)
    if bw == 0.0:
        return drawn
    return drawn + rng.normal(0.0, bw, size=drawn.shape)


def bootstrap_diff_ci(
    baseline: list[float],
    candidate: list[float],
    *,
    statistic: str | Statistic = "mean",
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int | None = 42,
    smooth: bool = False,
    min_reliable_n: int = 1,
) -> BootstrapResult:
    """Return a bootstrap CI for ``statistic(candidate) - statistic(baseline)``.

    Independently resamples each array with replacement; ``significant`` is
    ``True`` iff the resulting CI does not straddle zero *and* the result is
    ``reliable``. ``statistic`` is either a key in :data:`STATISTICS`
    (``"mean"`` / ``"p95"``) or a callable reducing a ``(n_resamples,
    sample_size)`` matrix along ``axis=1``.

    ``smooth`` enables a kernel-smoothed bootstrap (recommended for tail
    quantiles). ``min_reliable_n`` is the smallest per-array sample size for
    which the CI is trusted; below it the result is flagged unreliable and never
    significant.
    """
    if not baseline or not candidate:
        raise ValueError("baseline and candidate must each contain at least one value")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in the open interval (0, 1)")

    stat: Statistic = STATISTICS[statistic] if isinstance(statistic, str) else statistic

    rng = np.random.default_rng(seed)
    base_arr = np.asarray(baseline, dtype=np.float64)
    cand_arr = np.asarray(candidate, dtype=np.float64)

    n_effective = int(min(len(base_arr), len(cand_arr)))
    reliable = n_effective >= min_reliable_n

    base_idx = rng.integers(0, len(base_arr), size=(n_resamples, len(base_arr)))
    cand_idx = rng.integers(0, len(cand_arr), size=(n_resamples, len(cand_arr)))

    base_samples = _resample(base_arr, base_idx, rng, smooth=smooth)
    cand_samples = _resample(cand_arr, cand_idx, rng, smooth=smooth)

    diffs = stat(cand_samples) - stat(base_samples)
    alpha = (1.0 - confidence) / 2.0
    ci_low, ci_high = np.quantile(diffs, [alpha, 1.0 - alpha])
    # Point estimate uses the same statistic on the full (un-resampled) samples.
    delta = float(stat(cand_arr[None, :])[0] - stat(base_arr[None, :])[0])
    significant = bool((ci_low > 0 or ci_high < 0) and reliable)
    return BootstrapResult(
        delta=delta,
        ci_low=float(ci_low),
        ci_high=float(ci_high),
        significant=significant,
        reliable=reliable,
        n_effective=n_effective,
    )
