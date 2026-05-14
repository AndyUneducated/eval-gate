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


def test_empty_inputs_raise() -> None:
    with pytest.raises(ValueError, match="at least one value"):
        bootstrap_diff_ci([], [0.5])
    with pytest.raises(ValueError, match="at least one value"):
        bootstrap_diff_ci([0.5], [])


def test_invalid_confidence_raises() -> None:
    with pytest.raises(ValueError, match="confidence"):
        bootstrap_diff_ci([0.5], [0.5], confidence=1.5)
