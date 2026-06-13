"""Phase 15 engine: decision logic on hand-built paired streams."""

from __future__ import annotations

import numpy as np
import pytest

from evalgate.report.sequential import (
    Decision,
    SequentialGate,
    evaluate_sequential,
)


def test_obvious_regression_fails_early() -> None:
    rng = np.random.default_rng(0)
    n = 40
    base = rng.normal(0.85, 0.05, n)
    cand = base - 0.30 + rng.normal(0.0, 0.02, n)  # clear, low-variance drop
    out = evaluate_sequential(base, cand, look_every=5)
    assert out.decision == "fail"
    assert out.stopped_early
    assert out.cases_consumed < out.n_max
    assert out.looks[-1].decision == "fail"


def test_clean_candidate_passes_early_via_curtailment() -> None:
    rng = np.random.default_rng(7)
    n = 60
    base = rng.normal(0.7, 0.1, n)
    cand = rng.normal(0.7, 0.1, n)  # same distribution -> no real regression
    out = evaluate_sequential(base, cand, look_every=5)
    assert out.decision == "pass"
    assert out.stopped_early
    assert out.cases_consumed < out.n_max
    # The terminal look passed via conditional power below gamma.
    assert out.looks[-1].conditional_power < 0.2


def test_zero_variance_identical_runs_passes_at_exhaustion() -> None:
    base = [0.5] * 20
    cand = [0.5] * 20
    out = evaluate_sequential(base, cand, look_every=5)
    assert out.decision == "pass"
    assert not out.stopped_early
    assert out.cases_consumed == out.n_max


def test_zero_variance_uniform_drop_fails() -> None:
    base = [0.9] * 20
    cand = [0.4] * 20  # identical negative diff -> degenerate-but-certain regress
    out = evaluate_sequential(base, cand, look_every=5)
    assert out.decision == "fail"
    assert out.cases_consumed == 5  # first look


def test_gate_update_returns_none_between_looks() -> None:
    gate = SequentialGate(n_max=20, look_every=5)
    results = [gate.update(0.0) for _ in range(12)]
    # Looks only at n=5 and n=10 so far.
    assert results[0:4] == [None, None, None, None]
    assert results[4] is not None  # n == 5
    assert results[9] is not None  # n == 10
    assert results[10] is None and results[11] is None


def test_single_look_schedule_when_look_every_exceeds_n() -> None:
    base = [0.6, 0.6, 0.6]
    cand = [0.61, 0.59, 0.60]
    out = evaluate_sequential(base, cand, look_every=50)
    assert out.n_max == 3
    assert len(out.looks) == 1
    assert out.decision in {"pass", "fail"}


def test_final_look_forces_terminal_decision() -> None:
    gate = SequentialGate(n_max=10, look_every=5)
    decision = None
    for _ in range(10):
        decision = gate.update(0.001)  # tiny positive drift, never fails
    assert decision in (Decision.pass_, Decision.fail)
    assert gate.looks[-1].information_fraction == pytest.approx(1.0)
