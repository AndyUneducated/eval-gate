"""Phase 15 engine: spending functions, boundary recursion, normal helpers."""

from __future__ import annotations

import math

import pytest

from evalgate.report.sequential import (
    alpha_spent,
    compute_fail_boundaries,
    norm_cdf,
    norm_ppf,
)


@pytest.mark.parametrize("spending", ["obf", "pocock"])
def test_spending_endpoints_and_monotonic(spending: str) -> None:
    assert alpha_spent(spending, 0.0) == 0.0
    assert alpha_spent(spending, 1.0) == pytest.approx(0.05, abs=1e-6)
    prev = -1.0
    for i in range(0, 101):
        cur = alpha_spent(spending, i / 100.0)
        assert cur >= prev - 1e-12  # non-decreasing
        prev = cur


def test_unknown_spending_rejected() -> None:
    with pytest.raises(ValueError):
        alpha_spent("bogus", 0.5)


def test_norm_cdf_ppf_round_trip() -> None:
    for p in (1e-4, 0.01, 0.2, 0.5, 0.8, 0.975, 1 - 1e-4):
        assert norm_cdf(norm_ppf(p)) == pytest.approx(p, abs=1e-6)
    assert norm_cdf(0.0) == pytest.approx(0.5)
    assert math.isinf(norm_ppf(0.0)) and norm_ppf(0.0) < 0
    assert math.isinf(norm_ppf(1.0)) and norm_ppf(1.0) > 0


def test_boundary_recursion_spends_total_alpha() -> None:
    """The incremental spend across looks must sum to alpha by t=1."""
    t = [i / 20 for i in range(2, 21, 2)]
    total = 0.0
    prev = 0.0
    for tk in t:
        cur = alpha_spent("obf", tk)
        total += cur - prev
        prev = cur
    assert total == pytest.approx(0.05, abs=1e-6)


def test_obf_stricter_early_than_pocock() -> None:
    t = [i / 20 for i in range(2, 21, 2)]
    obf = compute_fail_boundaries(t, "obf")
    pocock = compute_fail_boundaries(t, "pocock")
    # O'Brien-Fleming barely spends early -> a *more extreme* (more negative)
    # early boundary than Pocock, but a less extreme final boundary.
    assert obf[0] < pocock[0]
    assert obf[-1] > pocock[-1]
    # Final one-sided boundary lands near the classic -1.65..-1.85 band.
    assert -2.2 < obf[-1] < -1.5


def test_zero_spend_look_never_fires() -> None:
    """OBF's first look at a tiny info-fraction is effectively unreachable."""
    z = compute_fail_boundaries([0.05, 0.5, 1.0], "obf")
    assert z[0] < -4.0  # extremely strict
