"""Phase 16 engine: ECE/MCE, reliability, sigmoid/logit, temperature scaling."""

from __future__ import annotations

import numpy as np
import pytest

from evalgate.report.calibration import (
    Calibrator,
    _logit,
    _sigmoid,
    evaluate_calibration,
    expected_calibration_error,
    fit_temperature,
    max_calibration_error,
    reliability_curve,
)


def test_sigmoid_logit_round_trip_and_clip() -> None:
    for p in (1e-4, 0.1, 0.5, 0.9, 1 - 1e-4):
        assert _sigmoid(_logit(p)) == pytest.approx(p, abs=1e-6)
    # Hard 0/1 clip to finite logits (no inf).
    assert np.isfinite(_logit(0.0)) and _logit(0.0) < 0
    assert np.isfinite(_logit(1.0)) and _logit(1.0) > 0


def test_perfectly_calibrated_has_low_ece() -> None:
    # Confidence == empirical accuracy in every bin -> ECE ~ 0.
    rng = np.random.default_rng(0)
    scores = rng.uniform(0, 1, 5000)
    labels = (rng.uniform(size=5000) < scores).astype(int)
    assert expected_calibration_error(scores, labels) < 0.03


def test_ece_mce_on_hand_built_bins() -> None:
    # Two tight clusters: conf 0.9 but acc 0.5; conf 0.1 but acc 0.5.
    scores = [0.9] * 10 + [0.1] * 10
    labels = [1, 0] * 5 + [1, 0] * 5  # 50% accuracy in each cluster
    ece = expected_calibration_error(scores, labels, n_bins=10)
    mce = max_calibration_error(scores, labels, n_bins=10)
    assert ece == pytest.approx(0.4, abs=1e-9)
    assert mce == pytest.approx(0.4, abs=1e-9)


def test_reliability_curve_only_nonempty_bins() -> None:
    scores = [0.05, 0.95, 0.96]
    labels = [0, 1, 1]
    pts = reliability_curve(scores, labels, n_bins=10)
    assert len(pts) == 2  # first bin + last bin
    last = pts[-1]
    assert last.count == 2
    assert last.mean_confidence == pytest.approx(0.955)
    assert last.mean_accuracy == pytest.approx(1.0)


def test_overconfidence_is_fixed_by_temperature_scaling() -> None:
    """The exit criterion: ECE >= 0.15 before, T > 1, ECE <= 0.05 after."""
    rng = np.random.default_rng(1)
    n = 2000
    true_p = rng.uniform(0.05, 0.95, n)
    labels = (rng.uniform(size=n) < true_p).astype(int)
    # Overconfident judge: sharpen logits by 1/T_true with T_true = 0.33.
    z = np.log(true_p / (1 - true_p))
    scores = 1.0 / (1.0 + np.exp(-z / 0.33))

    ece_before = expected_calibration_error(scores, labels)
    assert ece_before >= 0.15

    t = fit_temperature(scores, labels)
    assert t > 1.0  # recovers the over-sharpening

    stats = evaluate_calibration(scores, labels, Calibrator(t))
    assert stats.ece_before == pytest.approx(ece_before)
    assert stats.ece_after <= 0.05
    assert stats.mce_after < stats.mce_before


def test_fit_temperature_degenerate_returns_identity() -> None:
    assert fit_temperature([0.9] * 50, [1] * 50) == 1.0  # single class
    assert fit_temperature([0.9, 0.1], [1, 0]) == 1.0  # below min n


def test_calibrator_uncertainty_peaks_at_half() -> None:
    cal = Calibrator(temperature=1.0)
    assert cal.uncertainty(0.5) == pytest.approx(1.0)
    assert cal.uncertainty(0.99) < 0.1
    assert cal.uncertainty(0.01) < 0.1
    assert cal.transform(0.5) == pytest.approx(0.5, abs=1e-6)


def test_calibrator_dict_round_trip() -> None:
    cal = Calibrator(temperature=2.5)
    assert Calibrator.from_dict(cal.to_dict()).temperature == pytest.approx(2.5)
