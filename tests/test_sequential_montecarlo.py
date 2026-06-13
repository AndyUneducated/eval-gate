"""Phase 15 engine: Monte Carlo validation — the statistical exit criterion.

These are the load-bearing tests: they assert the gate actually controls the
cumulative false-fail rate (Type-I <= ~0.05 with binomial slack), has power
against an MDE-sized regression, and delivers the promised judge-call savings.
"""

from __future__ import annotations

import numpy as np

from evalgate.report.sequential import evaluate_sequential

SIMS = 1000


def _simulate(base_mu: float, cand_mu: float, *, n: int, seed: int, sd: float = 0.1):
    rng = np.random.default_rng(seed)
    fails = 0
    passes = 0
    consumed = []
    for _ in range(SIMS):
        base = rng.normal(base_mu, sd, n)
        cand = rng.normal(cand_mu, sd, n)
        out = evaluate_sequential(base, cand, look_every=5, mde=0.03, gamma=0.2)
        fails += out.decision == "fail"
        passes += out.decision == "pass"
        consumed.append(out.cases_consumed)
    return fails / SIMS, passes / SIMS, float(np.mean(consumed)) / n


def test_type_i_error_controlled_under_null() -> None:
    false_fail, _, _ = _simulate(0.7, 0.7, n=60, seed=1)
    # Cumulative alpha is 0.05; allow binomial slack over 1000 sims.
    assert false_fail <= 0.075


def test_power_and_savings_under_regression() -> None:
    power, _, frac = _simulate(0.7, 0.6, n=60, seed=2)  # -0.10 drift, > mde
    assert power >= 0.8
    assert frac <= 0.5  # >= 50% judge-call savings on obvious regressions


def test_clean_candidate_mostly_passes_early_with_savings() -> None:
    false_fail, pass_rate, frac = _simulate(0.7, 0.7, n=60, seed=3)
    assert pass_rate >= 0.9
    assert false_fail <= 0.075
    assert frac <= 0.5  # >= 50% savings on obviously-fine candidates


def test_pocock_also_controls_type_i() -> None:
    # Pocock front-loads alpha, so its earliest interim looks lean on the normal
    # approximation to a small-n t-statistic (mildly anti-conservative at n=5).
    # With a realistic cadence (first look at n=10) it controls Type-I; OBF — the
    # default — is robust even at look_every=5 because it barely spends early.
    rng = np.random.default_rng(11)
    fails = 0
    for _ in range(SIMS):
        base = rng.normal(0.7, 0.1, 60)
        cand = rng.normal(0.7, 0.1, 60)
        out = evaluate_sequential(base, cand, look_every=10, spending="pocock")
        fails += out.decision == "fail"
    assert fails / SIMS <= 0.075
