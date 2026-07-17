from __future__ import annotations

import numpy as np
import pytest

from evalgate.report.significance import bootstrap_diff_ci


def test_identical_inputs_are_not_significant() -> None:
    result = bootstrap_diff_ci([0.8] * 30, [0.8] * 30, seed=0)
    assert not result.significant
    assert result.delta == 0.0
    assert result.ci_low <= 0.0 <= result.ci_high


def test_large_drop_is_significant_and_signed() -> None:
    rng = np.random.default_rng(0)
    baseline = rng.normal(0.9, 0.05, 100).tolist()
    candidate = rng.normal(0.7, 0.05, 100).tolist()
    result = bootstrap_diff_ci(baseline, candidate, seed=0)
    assert result.significant
    assert result.delta < 0
    assert result.ci_high < 0
    assert result.direction == "down"


def test_small_drop_below_noise_floor_is_not_significant() -> None:
    rng = np.random.default_rng(1)
    baseline = rng.normal(0.9, 0.1, 30).tolist()
    candidate = rng.normal(0.895, 0.1, 30).tolist()
    result = bootstrap_diff_ci(baseline, candidate, seed=0)
    assert not result.significant


def test_p95_statistic_point_estimate_and_significance() -> None:
    # Point estimate uses p95, not mean: a heavy tail must move the delta.
    base = [100.0] * 19 + [200.0]
    cand = [100.0] * 19 + [900.0]
    result = bootstrap_diff_ci(base, cand, statistic="p95", seed=0)
    assert result.delta > 0  # p95 tail worsened
    assert result.direction == "up"


def test_p95_identical_tails_not_significant() -> None:
    rng = np.random.default_rng(3)
    sample = rng.normal(1000, 50, 40).tolist()
    result = bootstrap_diff_ci(sample, list(sample), statistic="p95", seed=0)
    assert not result.significant


def test_mean_result_is_reliable_by_default() -> None:
    result = bootstrap_diff_ci([0.8] * 5, [0.6] * 5, seed=0)
    assert result.reliable is True
    assert result.n_effective == 5


def test_p95_small_sample_is_flagged_unreliable_and_never_significant() -> None:
    # A big tail jump on a tiny sample: the naive CI would call it significant,
    # but with too few points to support a p95 the guard refuses to block.
    base = [100.0] * 8
    cand = [100.0] * 7 + [9000.0]
    result = bootstrap_diff_ci(base, cand, statistic="p95", smooth=True, min_reliable_n=20, seed=0)
    assert result.reliable is False
    assert result.significant is False
    assert result.n_effective == 8


def test_p95_smoothed_detects_real_tail_regression_with_enough_data() -> None:
    rng = np.random.default_rng(7)
    base = rng.normal(1000, 80, 60).tolist()
    cand = rng.normal(1000, 80, 60).tolist()
    cand = [x * 1.5 for x in cand]  # 50% slower tail
    result = bootstrap_diff_ci(base, cand, statistic="p95", smooth=True, min_reliable_n=20, seed=0)
    assert result.reliable is True
    assert result.significant is True
    assert result.direction == "up"


def test_unknown_statistic_raises() -> None:
    with pytest.raises(KeyError):
        bootstrap_diff_ci([1.0], [1.0], statistic="median")


def test_empty_inputs_raise() -> None:
    with pytest.raises(ValueError, match="at least one value"):
        bootstrap_diff_ci([], [0.5])
    with pytest.raises(ValueError, match="at least one value"):
        bootstrap_diff_ci([0.5], [])


def test_invalid_confidence_raises() -> None:
    with pytest.raises(ValueError, match="confidence"):
        bootstrap_diff_ci([0.5], [0.5], confidence=1.5)
