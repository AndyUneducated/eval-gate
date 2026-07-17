"""Judge-score calibration engine (Phase 16) — pure statistics, no DB / no LLM.

The judge emits a quality ``score`` in ``[0, 1]`` that we *want* to read as a
probability: "score 0.8 -> ~80% chance a human calls this good". Raw judge
scores rarely have that property — they're systematically over- or
under-confident. This module measures that gap (**ECE / MCE + reliability
diagram**) and fixes it with single-parameter **temperature scaling** (Guo et
al. 2017): calibrate on the logit, ``p = sigmoid(logit(score) / T)``.

A fitted :class:`Calibrator` then turns a raw score into a calibrated ``P(good)``
and a calibrated **uncertainty** (``1 - |2p-1|``, maximal at ``p=0.5``) — the
active-learning signal BadCase uncertainty sampling ranks on.

Only numpy is available (no scipy/sklearn), so the temperature fit is an in-house
1-D convex NLL minimization (golden-section search). ``matplotlib`` is imported
lazily inside :func:`render_reliability_png` so the stats path stays
dependency-light and testable headless.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

DEFAULT_N_BINS = 10
_EPS = 1e-6
_MIN_LABELS = 10  # below this (or single-class) we don't trust a fit


# --------------------------------------------------------------------------- #
# Sigmoid / logit
# --------------------------------------------------------------------------- #


def _sigmoid(x: float) -> float:
    # Stable: avoids overflow for large |x|.
    return 0.5 * (1.0 + math.tanh(0.5 * x))


def _logit(p: float) -> float:
    p = min(max(p, _EPS), 1.0 - _EPS)
    return math.log(p / (1.0 - p))


def _logit_array(scores: np.ndarray) -> np.ndarray:
    p = np.clip(scores, _EPS, 1.0 - _EPS)
    return np.log(p / (1.0 - p))


# --------------------------------------------------------------------------- #
# Calibration error metrics
# --------------------------------------------------------------------------- #


@dataclass
class ReliabilityPoint:
    bin_lower: float
    bin_upper: float
    count: int
    mean_confidence: float
    mean_accuracy: float


def reliability_curve(
    scores: Sequence[float],
    labels: Sequence[int],
    n_bins: int = DEFAULT_N_BINS,
) -> list[ReliabilityPoint]:
    """Per-bin (mean confidence, empirical accuracy) over equal-width bins.

    Returns only non-empty bins (the points a reliability diagram plots).
    """
    s = np.asarray(scores, dtype=np.float64)
    y = np.asarray(labels, dtype=np.float64)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    points: list[ReliabilityPoint] = []
    for k in range(n_bins):
        lo, hi = edges[k], edges[k + 1]
        # Last bin is closed on the right so score==1.0 lands somewhere.
        mask = (s >= lo) & (s < hi) if k < n_bins - 1 else (s >= lo) & (s <= hi)
        count = int(mask.sum())
        if count == 0:
            continue
        points.append(
            ReliabilityPoint(
                bin_lower=float(lo),
                bin_upper=float(hi),
                count=count,
                mean_confidence=float(s[mask].mean()),
                mean_accuracy=float(y[mask].mean()),
            )
        )
    return points


def expected_calibration_error(
    scores: Sequence[float],
    labels: Sequence[int],
    n_bins: int = DEFAULT_N_BINS,
) -> float:
    """ECE = sum over bins of (|bin| / N) * |accuracy - confidence|."""
    points = reliability_curve(scores, labels, n_bins)
    n = len(scores)
    if n == 0:
        return 0.0
    return sum((p.count / n) * abs(p.mean_accuracy - p.mean_confidence) for p in points)


def max_calibration_error(
    scores: Sequence[float],
    labels: Sequence[int],
    n_bins: int = DEFAULT_N_BINS,
) -> float:
    """MCE = max over non-empty bins of |accuracy - confidence|."""
    points = reliability_curve(scores, labels, n_bins)
    if not points:
        return 0.0
    return max(abs(p.mean_accuracy - p.mean_confidence) for p in points)


# --------------------------------------------------------------------------- #
# Temperature scaling
# --------------------------------------------------------------------------- #


def _nll(w: float, z: np.ndarray, y: np.ndarray) -> float:
    """Mean logistic loss for calibrated logits ``w * z`` (w = 1/T).

    Stable via ``logaddexp``: ``-log sigmoid(t) = softplus(-t)``.
    """
    t = w * z
    return float(np.mean(y * np.logaddexp(0.0, -t) + (1.0 - y) * np.logaddexp(0.0, t)))


def _golden_section_min(f, a: float, b: float, *, tol: float = 1e-6, iters: int = 200):
    inv_phi = (math.sqrt(5.0) - 1.0) / 2.0
    c = b - inv_phi * (b - a)
    d = a + inv_phi * (b - a)
    fc, fd = f(c), f(d)
    for _ in range(iters):
        if b - a < tol:
            break
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - inv_phi * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + inv_phi * (b - a)
            fd = f(d)
    return 0.5 * (a + b)


def fit_temperature(
    scores: Sequence[float],
    labels: Sequence[int],
    *,
    w_lo: float = 0.05,
    w_hi: float = 20.0,
) -> float:
    """Fit T>0 minimizing logistic NLL of ``sigmoid(logit(score)/T)`` vs labels.

    The loss is convex in ``w = 1/T`` (logistic regression with one feature, the
    logit, no intercept), so a 1-D golden-section search is exact. Returns
    ``1.0`` (identity) when the label set is degenerate (single class or
    fewer than ``_MIN_LABELS`` rows) — not enough signal to trust a fit.
    """
    y = np.asarray(labels, dtype=np.float64)
    if len(y) < _MIN_LABELS or y.min() == y.max():
        return 1.0
    z = _logit_array(np.asarray(scores, dtype=np.float64))
    w = _golden_section_min(lambda ww: _nll(ww, z, y), w_lo, w_hi)
    return 1.0 / max(w, 1e-9)


# --------------------------------------------------------------------------- #
# Calibrator + bundled stats
# --------------------------------------------------------------------------- #


GLOBAL_SCOPE = "global"
VALID_SCOPES = (GLOBAL_SCOPE, "task_type", "judge_model")


@dataclass
class Calibrator:
    """A fitted temperature (or family of them) mapping raw scores to P(good).

    Phase 16 shipped a single global ``temperature``. Phase 17 generalizes this
    to *conditional* calibration: a judge is often over-confident on one
    task_type but well-calibrated on another (likewise across judge models), so
    one global curve leaves ECE on the table. When ``scope`` is not ``"global"``
    the calibrator holds a per-group temperature in ``group_temperatures`` and
    selects it by the row's group key at transform time, falling back to the
    global ``temperature`` for any group it wasn't fitted on (unseen /
    data-thin groups). ``scope == "global"`` keeps the exact Phase 16 behavior.
    """

    temperature: float
    scope: str = GLOBAL_SCOPE
    group_temperatures: dict[str, float] = field(default_factory=dict)

    def temperature_for(self, group: str | None = None) -> float:
        """The temperature for ``group`` — its own if fitted, else the global."""
        if group is not None:
            t = self.group_temperatures.get(group)
            if t is not None:
                return t
        return self.temperature

    def transform(self, score: float, group: str | None = None) -> float:
        return _sigmoid(_logit(score) / self.temperature_for(group))

    def transform_array(
        self, scores: Sequence[float], groups: Sequence[str | None] | None = None
    ) -> np.ndarray:
        z = _logit_array(np.asarray(scores, dtype=np.float64))
        if groups is None or not self.group_temperatures:
            t = z / self.temperature
        else:
            temps = np.array([self.temperature_for(g) for g in groups], dtype=np.float64)
            t = z / temps
        return 0.5 * (1.0 + np.tanh(0.5 * t))

    def uncertainty(self, score: float, group: str | None = None) -> float:
        """Active-learning uncertainty: 1 at p=0.5, 0 at p in {0, 1}."""
        p = self.transform(score, group)
        return 1.0 - abs(2.0 * p - 1.0)

    def to_dict(self) -> dict:
        out: dict = {"temperature": self.temperature, "scope": self.scope}
        if self.group_temperatures:
            out["group_temperatures"] = dict(self.group_temperatures)
        return out

    @classmethod
    def from_dict(cls, data: dict) -> Calibrator:
        # Accept both the minimal ``{temperature}`` form and the on-disk params
        # file, whose ``groups`` map name -> {temperature, n, ece_*}.
        raw_groups = data.get("group_temperatures") or data.get("groups") or {}
        group_temperatures: dict[str, float] = {}
        for name, value in raw_groups.items():
            group_temperatures[name] = float(
                value["temperature"] if isinstance(value, dict) else value
            )
        return cls(
            temperature=float(data["temperature"]),
            scope=str(data.get("scope", GLOBAL_SCOPE)),
            group_temperatures=group_temperatures,
        )


@dataclass
class CalibrationStats:
    n: int
    n_bins: int
    temperature: float
    ece_before: float
    ece_after: float
    mce_before: float
    mce_after: float
    reliability_before: list[ReliabilityPoint] = field(default_factory=list)
    reliability_after: list[ReliabilityPoint] = field(default_factory=list)


def evaluate_calibration(
    scores: Sequence[float],
    labels: Sequence[int],
    calibrator: Calibrator,
    *,
    n_bins: int = DEFAULT_N_BINS,
    groups: Sequence[str | None] | None = None,
) -> CalibrationStats:
    """Compute ECE/MCE + reliability before and after applying ``calibrator``.

    When ``groups`` is given (aligned to ``scores``) each score is calibrated
    with its group's temperature, so the "after" numbers reflect the conditional
    fit rather than a single global curve.
    """
    calibrated = calibrator.transform_array(scores, groups=groups)
    return CalibrationStats(
        n=len(scores),
        n_bins=n_bins,
        temperature=calibrator.temperature,
        ece_before=expected_calibration_error(scores, labels, n_bins),
        ece_after=expected_calibration_error(calibrated, labels, n_bins),
        mce_before=max_calibration_error(scores, labels, n_bins),
        mce_after=max_calibration_error(calibrated, labels, n_bins),
        reliability_before=reliability_curve(scores, labels, n_bins),
        reliability_after=reliability_curve(calibrated, labels, n_bins),
    )


def render_reliability_png(stats: CalibrationStats, path: str) -> None:
    """Render a before/after reliability diagram to ``path`` (lazy matplotlib)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot([0, 1], [0, 1], linestyle="--", label="perfect calibration")
    if stats.reliability_before:
        ax.plot(
            [p.mean_confidence for p in stats.reliability_before],
            [p.mean_accuracy for p in stats.reliability_before],
            marker="o",
            label=f"raw (ECE={stats.ece_before:.3f})",
        )
    if stats.reliability_after:
        ax.plot(
            [p.mean_confidence for p in stats.reliability_after],
            [p.mean_accuracy for p in stats.reliability_after],
            marker="s",
            label=f"calibrated T={stats.temperature:.2f} (ECE={stats.ece_after:.3f})",
        )
    ax.set_xlabel("confidence (judge score)")
    ax.set_ylabel("empirical accuracy (human good-rate)")
    ax.set_title("Reliability diagram")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
