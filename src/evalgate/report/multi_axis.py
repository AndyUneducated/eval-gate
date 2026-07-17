"""Build the CI gate report (quality / cost / latency_p95 / safety).

Every numeric axis is judged by the *same* machinery: a bootstrap CI on its
aggregate statistic (mean for quality/cost, p95 for latency) plus an optional
per-axis relative-tolerance band. A regression requires the delta to be (a) in
the bad direction, (b) statistically significant, and (c) larger than the axis'
tolerance band — so noisy tail latency no longer trips the gate on a sub-percent
wobble. Safety is breakdown-only: no scalar — ``passed = all(sub_metrics.passed)``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from evalgate.core.schemas import AxisMetric, EvalRecord, RecordInput, coerce_records
from evalgate.report.significance import bootstrap_diff_ci

Direction = Literal["higher_is_better", "lower_is_better"]
Aggregator = Literal["mean", "p95"]

# p95 latency on real local inference wobbles run-to-run; require a candidate
# regression to exceed this fraction of the baseline p95 (and be significant)
# before it fails the gate.
LATENCY_REL_TOLERANCE = 0.10

# Phase 17: a p95 bootstrap only becomes trustworthy once the tail has enough
# support. Below this per-side sample count the latency axis is flagged
# unreliable and can't fail the gate (see report/significance.py). ~20 rows puts
# 1 observation past the 95th percentile, the minimum for a non-degenerate tail.
P95_MIN_RELIABLE_N = 20


@dataclass(frozen=True)
class AxisSpec:
    """Declarative description of one gate axis.

    A *scalar* axis (quality / cost / latency) has an ``extractor`` + an
    ``aggregator`` and is judged by a bootstrap CI on that aggregate. A
    *breakdown-only* axis (safety) has ``extractor=None`` / ``aggregator=None``:
    it carries no scalar, only nested per-sub-metric axes derived from
    ``axis_breakdown[name]``, and ``passed = all(sub.passed)``. Making safety a
    plain ``AXES`` entry removes the bespoke branch that used to special-case it.
    """

    name: str
    direction: Direction
    extractor: Callable[[EvalRecord], float] | None
    aggregator: Aggregator | None
    # Minimum |delta| as a fraction of the baseline aggregate before a
    # (significant, bad-direction) change counts as a regression. 0 = no band.
    rel_tolerance: float = 0.0
    # When True, derive nested sub-axes from ``axis_breakdown[name]`` (each its
    # own bootstrap-CI mean axis, ``direction`` inherited from the parent).
    has_sub_metrics: bool = False


def _mean(values: Sequence[float]) -> float:
    return float(np.mean(values)) if len(values) else 0.0


def _p95(values: Sequence[float]) -> float:
    return float(np.percentile(values, 95)) if len(values) else 0.0


AXES: tuple[AxisSpec, ...] = (
    AxisSpec(
        name="quality",
        direction="higher_is_better",
        extractor=lambda r: float(r.score),
        aggregator="mean",
        has_sub_metrics=True,
    ),
    AxisSpec(
        name="cost",
        direction="lower_is_better",
        extractor=lambda r: float(r.cost_usd),
        aggregator="mean",
    ),
    AxisSpec(
        name="latency_p95",
        direction="lower_is_better",
        extractor=lambda r: float(r.latency_ms),
        aggregator="p95",
        rel_tolerance=LATENCY_REL_TOLERANCE,
    ),
    AxisSpec(
        name="safety",
        direction="lower_is_better",
        extractor=None,
        aggregator=None,
        has_sub_metrics=True,
    ),
)


def _aggregate(spec: AxisSpec, values: Sequence[float]) -> float:
    return _mean(values) if spec.aggregator == "mean" else _p95(values)


def _is_regression(
    *,
    direction: Direction,
    delta: float,
    baseline_agg: float,
    significant: bool,
    rel_tolerance: float,
) -> bool:
    """A regression = significant + bad-direction + outside the tolerance band."""
    if not significant:
        return False
    bad_direction = delta < 0 if direction == "higher_is_better" else delta > 0
    if not bad_direction:
        return False
    if rel_tolerance > 0.0:
        return abs(delta) >= rel_tolerance * abs(baseline_agg)
    return True


def build_axis_metrics(
    baseline: Sequence[RecordInput],
    candidate: Sequence[RecordInput],
) -> list[AxisMetric]:
    baseline = coerce_records(baseline)
    candidate = coerce_records(candidate)
    metrics: list[AxisMetric] = []
    for spec in AXES:
        b_agg = c_agg = delta = 0.0
        ci_low: float | None = None
        ci_high: float | None = None
        significant = False
        regressed = False

        # Scalar axes (extractor + aggregator) get a bootstrap-CI verdict on
        # their aggregate; breakdown-only axes (safety) carry no scalar.
        if spec.extractor is not None and spec.aggregator is not None:
            b_vals = [spec.extractor(r) for r in baseline]
            c_vals = [spec.extractor(r) for r in candidate]
            b_agg = _aggregate(spec, b_vals)
            c_agg = _aggregate(spec, c_vals)
            delta = c_agg - b_agg
            if b_vals and c_vals:
                # Tail quantiles get the smoothed + sample-size-guarded bootstrap
                # (Phase 17); mean axes keep the plain nonparametric one.
                is_tail = spec.aggregator == "p95"
                boot = bootstrap_diff_ci(
                    b_vals,
                    c_vals,
                    statistic=spec.aggregator,
                    smooth=is_tail,
                    min_reliable_n=P95_MIN_RELIABLE_N if is_tail else 1,
                )
                ci_low = boot.ci_low
                ci_high = boot.ci_high
                significant = boot.significant
                regressed = _is_regression(
                    direction=spec.direction,
                    delta=delta,
                    baseline_agg=b_agg,
                    significant=significant,
                    rel_tolerance=spec.rel_tolerance,
                )

        sub_metrics: dict[str, AxisMetric] | None = None
        sub_regressed = False
        if spec.has_sub_metrics:
            sub_metrics = _build_sub_metric_axes(
                baseline,
                candidate,
                axis_name=spec.name,
                direction=spec.direction,
            )
            if sub_metrics:
                sub_regressed = any(not s.passed for s in sub_metrics.values())

        # A breakdown-only axis with no data at all (no records carry its
        # axis_breakdown bucket) is omitted entirely, matching the prior
        # "safety only shows up when there are safety sub-metrics" behavior.
        if spec.aggregator is None and sub_metrics is None:
            continue

        metrics.append(
            AxisMetric(
                name=spec.name,
                baseline=b_agg,
                candidate=c_agg,
                delta=delta,
                ci_low=ci_low,
                ci_high=ci_high,
                significant=significant,
                passed=not (regressed or sub_regressed),
                sub_metrics=sub_metrics,
            )
        )

    return metrics


def _build_sub_metric_axes(
    baseline: Sequence[EvalRecord],
    candidate: Sequence[EvalRecord],
    *,
    axis_name: str,
    direction: Direction,
) -> dict[str, AxisMetric] | None:
    names: set[str] = set()
    for rec in list(baseline) + list(candidate):
        bucket = _bucket(rec, axis_name)
        if bucket:
            names.update(str(k) for k in bucket)
    if not names:
        return None

    out: dict[str, AxisMetric] = {}
    for name in sorted(names):
        b_vals = _pluck_metric(baseline, axis_name=axis_name, metric=name)
        c_vals = _pluck_metric(candidate, axis_name=axis_name, metric=name)
        b_agg = _mean(b_vals)
        c_agg = _mean(c_vals)
        delta = c_agg - b_agg
        if b_vals and c_vals:
            boot = bootstrap_diff_ci(b_vals, c_vals)
            ci_low: float | None = boot.ci_low
            ci_high: float | None = boot.ci_high
            significant = boot.significant
        else:
            ci_low = ci_high = None
            significant = False
        if direction == "higher_is_better":
            regressed = significant and delta < 0
        else:
            regressed = significant and delta > 0
        out[name] = AxisMetric(
            name=name,
            baseline=b_agg,
            candidate=c_agg,
            delta=delta,
            ci_low=ci_low,
            ci_high=ci_high,
            significant=significant,
            passed=not regressed,
        )
    return out


def _bucket(rec: EvalRecord, axis_name: str) -> dict[str, Any] | None:
    breakdown = rec.axis_breakdown
    if not isinstance(breakdown, dict):
        return None
    inner = breakdown.get(axis_name)
    return inner if isinstance(inner, dict) else None


def _pluck_metric(
    records: Sequence[EvalRecord],
    *,
    axis_name: str,
    metric: str,
) -> list[float]:
    out: list[float] = []
    for rec in records:
        bucket = _bucket(rec, axis_name)
        if bucket is None or metric not in bucket:
            continue
        try:
            out.append(float(bucket[metric]))
        except (TypeError, ValueError):
            continue
    return out
