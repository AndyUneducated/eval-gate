"""Judge-vs-human agreement engine (Phase 17) — pure statistics, no DB / no LLM.

Calibration (Phase 16) answers "is the judge's *score* a real probability?".
This module answers the complementary question: "does the judge's binary
*verdict* agree with a human's, beyond what you'd get by chance?" — the headline
Cohen's kappa (Cohen 1960) the design doc targets (~0.85, approaching the
double-human ceiling).

We binarize the judge's ``score`` at a decision ``threshold`` (default 0.5) into
good/bad, pair it with the human good/bad label, and report:

- **Cohen's kappa** ``(p_o - p_e) / (1 - p_e)`` — observed agreement corrected
  for the agreement expected from each rater's marginal rates. 1 = perfect, 0 =
  chance, <0 = worse than chance.
- the raw confusion counts, observed / expected agreement, each rater's
  positive rate, and a bootstrap CI for kappa (the same resampling idea the gate
  uses for regression significance).

Only numpy — no sklearn. Kappa is a 2x2 closed form, so this stays dependency
light and headless-testable, exactly like ``report/calibration.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

DEFAULT_THRESHOLD = 0.5


def binarize_scores(scores: Sequence[float], *, threshold: float = DEFAULT_THRESHOLD) -> list[int]:
    """Judge verdict: ``score >= threshold`` -> 1 (good), else 0 (bad)."""
    return [1 if float(s) >= threshold else 0 for s in scores]


@dataclass(frozen=True)
class Confusion:
    """2x2 counts of (judge verdict, human label), both in {0, 1}."""

    tp: int  # judge good & human good
    fp: int  # judge good & human bad
    fn: int  # judge bad  & human good
    tn: int  # judge bad  & human bad

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.fn + self.tn


def confusion_counts(
    judge: Sequence[int] | np.ndarray, human: Sequence[int] | np.ndarray
) -> Confusion:
    j = np.asarray(judge, dtype=int)
    h = np.asarray(human, dtype=int)
    if len(j) != len(h):
        raise ValueError("judge and human label sequences must be the same length")
    tp = int(np.sum((j == 1) & (h == 1)))
    fp = int(np.sum((j == 1) & (h == 0)))
    fn = int(np.sum((j == 0) & (h == 1)))
    tn = int(np.sum((j == 0) & (h == 0)))
    return Confusion(tp=tp, fp=fp, fn=fn, tn=tn)


def _kappa_from_confusion(c: Confusion) -> tuple[float, float, float]:
    """Return ``(kappa, observed_agreement, expected_agreement)``.

    Degenerate ``p_e == 1`` (every rating in one class for both raters) makes
    kappa undefined; we return ``1.0`` when they also perfectly agree and
    ``0.0`` otherwise — the conventional convention.
    """
    n = c.n
    if n == 0:
        return 0.0, 0.0, 0.0
    p_o = (c.tp + c.tn) / n
    judge_pos = (c.tp + c.fp) / n
    human_pos = (c.tp + c.fn) / n
    p_e = judge_pos * human_pos + (1.0 - judge_pos) * (1.0 - human_pos)
    if p_e >= 1.0:
        return (1.0 if p_o >= 1.0 else 0.0), p_o, p_e
    kappa = (p_o - p_e) / (1.0 - p_e)
    return kappa, p_o, p_e


def cohen_kappa(judge: Sequence[int], human: Sequence[int]) -> float:
    return _kappa_from_confusion(confusion_counts(judge, human))[0]


@dataclass
class AgreementStats:
    n: int
    threshold: float
    cohen_kappa: float
    observed_agreement: float
    expected_agreement: float
    ci_low: float
    ci_high: float
    judge_positive_rate: float
    human_positive_rate: float
    confusion: Confusion


def _bootstrap_kappa_ci(
    judge: np.ndarray,
    human: np.ndarray,
    *,
    n_resamples: int,
    confidence: float,
    seed: int | None,
) -> tuple[float, float]:
    n = len(judge)
    if n < 2:
        return 0.0, 0.0
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(n_resamples, n))
    kappas = np.empty(n_resamples, dtype=np.float64)
    for i in range(n_resamples):
        ji = judge[idx[i]]
        hi = human[idx[i]]
        kappas[i] = _kappa_from_confusion(confusion_counts(ji, hi))[0]
    alpha = (1.0 - confidence) / 2.0
    lo, hi = np.quantile(kappas, [alpha, 1.0 - alpha])
    return float(lo), float(hi)


def evaluate_agreement(
    scores: Sequence[float],
    human_labels: Sequence[int],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int | None = 42,
) -> AgreementStats:
    """Cohen's kappa (+ confusion, marginals, bootstrap CI) of judge vs human."""
    judge = np.asarray(binarize_scores(scores, threshold=threshold), dtype=int)
    human = np.asarray(human_labels, dtype=int)
    confusion = confusion_counts(judge, human)
    kappa, p_o, p_e = _kappa_from_confusion(confusion)
    ci_low, ci_high = _bootstrap_kappa_ci(
        judge, human, n_resamples=n_resamples, confidence=confidence, seed=seed
    )
    n = confusion.n
    judge_pos = (confusion.tp + confusion.fp) / n if n else 0.0
    human_pos = (confusion.tp + confusion.fn) / n if n else 0.0
    return AgreementStats(
        n=n,
        threshold=threshold,
        cohen_kappa=kappa,
        observed_agreement=p_o,
        expected_agreement=p_e,
        ci_low=ci_low,
        ci_high=ci_high,
        judge_positive_rate=judge_pos,
        human_positive_rate=human_pos,
        confusion=confusion,
    )
