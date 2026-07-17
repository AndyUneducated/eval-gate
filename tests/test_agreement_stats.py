"""Phase 17 engine: Cohen's kappa between judge verdict and human labels."""

from __future__ import annotations

import numpy as np
import pytest

from evalgate.report.agreement import (
    binarize_scores,
    cohen_kappa,
    confusion_counts,
    evaluate_agreement,
)


def test_binarize_thresholds_at_half() -> None:
    assert binarize_scores([0.0, 0.49, 0.5, 0.51, 1.0]) == [0, 0, 1, 1, 1]
    assert binarize_scores([0.6, 0.7], threshold=0.65) == [0, 1]


def test_perfect_agreement_is_kappa_one() -> None:
    scores = [0.9, 0.8, 0.1, 0.2, 0.95, 0.05]
    human = [1, 1, 0, 0, 1, 0]  # matches the >=0.5 verdict exactly
    stats = evaluate_agreement(scores, human)
    assert stats.cohen_kappa == pytest.approx(1.0)
    assert stats.observed_agreement == pytest.approx(1.0)
    assert stats.confusion.fp == 0 and stats.confusion.fn == 0


def test_chance_level_agreement_is_near_zero() -> None:
    # Judge says good for the first half, human labels are independent coin flips
    # arranged to give ~50% agreement with balanced marginals -> kappa ~ 0.
    judge = [1, 1, 0, 0]
    human = [1, 0, 1, 0]
    assert cohen_kappa(judge, human) == pytest.approx(0.0, abs=1e-9)


def test_confusion_counts_partition_sample() -> None:
    judge = [1, 1, 0, 0, 1]
    human = [1, 0, 0, 1, 1]
    c = confusion_counts(judge, human)
    assert (c.tp, c.fp, c.fn, c.tn) == (2, 1, 1, 1)
    assert c.n == 5


def test_length_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="same length"):
        confusion_counts([1, 0], [1])


def test_degenerate_single_class_is_defined() -> None:
    # Both raters always "good": p_e == 1; convention -> perfect agreement = 1.
    assert cohen_kappa([1, 1, 1], [1, 1, 1]) == pytest.approx(1.0)
    # Judge always good, human always bad: total disagreement -> 0.0.
    assert cohen_kappa([1, 1, 1], [0, 0, 0]) == pytest.approx(0.0)


def test_bootstrap_ci_brackets_kappa_for_strong_agreement() -> None:
    rng = np.random.default_rng(0)
    n = 400
    human = rng.integers(0, 2, n)
    # Judge agrees 90% of the time -> high, tight-ish kappa CI well above 0.
    flip = rng.uniform(size=n) < 0.10
    judge_bit = np.where(flip, 1 - human, human)
    scores = np.where(judge_bit == 1, 0.8, 0.2).tolist()
    stats = evaluate_agreement(scores, human.tolist(), seed=0)
    assert stats.cohen_kappa > 0.6
    assert stats.ci_low <= stats.cohen_kappa <= stats.ci_high
    assert stats.ci_low > 0.0
