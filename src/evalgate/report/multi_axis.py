"""Build the CI gate report (quality / cost / latency_p95 / safety).

Mean-aggregated axes use bootstrap CI; p95 uses a simple threshold delta.
Safety is breakdown-only: no mean scalar — ``passed = all(sub_metrics.passed)``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from evalgate.core.schemas import AxisMetric
from evalgate.report.significance import bootstrap_diff_ci

Direction = Literal["higher_is_better", "lower_is_better"]
Aggregator = Literal["mean", "p95"]

EvalRecord = dict[str, Any]

SAFETY_AXIS = "safety"
SAFETY_DIRECTION: Direction = "lower_is_better"


@dataclass(frozen=True)
class AxisSpec:
    name: str
    direction: Direction
    extractor: Callable[[EvalRecord], float]
    aggregator: Aggregator


def _mean(values: Sequence[float]) -> float:
    return float(np.mean(values)) if len(values) else 0.0


def _p95(values: Sequence[float]) -> float:
    return float(np.percentile(values, 95)) if len(values) else 0.0


AXES: tuple[AxisSpec, ...] = (
    AxisSpec(
        name="quality",
        direction="higher_is_better",
        extractor=lambda r: float(r.get("score", 0.0)),
        aggregator="mean",
    ),
    AxisSpec(
        name="cost",
        direction="lower_is_better",
        extractor=lambda r: float(r.get("cost_usd", 0.0)),
        aggregator="mean",
    ),
    AxisSpec(
        name="latency_p95",
        direction="lower_is_better",
        extractor=lambda r: float(r.get("latency_ms", 0.0)),
        aggregator="p95",
    ),
)

_SUB_AXIS_PARENTS: dict[str, Direction] = {
    "quality": "higher_is_better",
}


def _aggregate(spec: AxisSpec, values: Sequence[float]) -> float:
    return _mean(values) if spec.aggregator == "mean" else _p95(values)


def build_axis_metrics(
    baseline: Sequence[EvalRecord],
    candidate: Sequence[EvalRecord],
) -> list[AxisMetric]:
    metrics: list[AxisMetric] = []
    for spec in AXES:
        b_vals = [spec.extractor(r) for r in baseline]
        c_vals = [spec.extractor(r) for r in candidate]
        b_agg = _aggregate(spec, b_vals)
        c_agg = _aggregate(spec, c_vals)
        delta = c_agg - b_agg

        if spec.aggregator == "mean" and b_vals and c_vals:
            boot = bootstrap_diff_ci(b_vals, c_vals)
            ci_low = boot.ci_low
            ci_high = boot.ci_high
            significant = boot.significant
            if spec.direction == "higher_is_better":
                regressed = significant and delta < 0
            else:
                regressed = significant and delta > 0
        elif spec.aggregator == "p95" and b_vals and c_vals:
            ci_low = ci_high = None
            significant = False
            regressed = delta < 0 if spec.direction == "higher_is_better" else delta > 0
        else:
            ci_low = ci_high = None
            significant = False
            regressed = False

        sub_metrics: dict[str, AxisMetric] | None = None
        sub_regressed = False
        if spec.name in _SUB_AXIS_PARENTS:
            sub_metrics = _build_sub_metric_axes(
                baseline,
                candidate,
                axis_name=spec.name,
                direction=_SUB_AXIS_PARENTS[spec.name],
            )
            if sub_metrics:
                sub_regressed = any(not s.passed for s in sub_metrics.values())

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

    safety_subs = _build_sub_metric_axes(
        baseline,
        candidate,
        axis_name=SAFETY_AXIS,
        direction=SAFETY_DIRECTION,
    )
    if safety_subs:
        sub_regressed = any(not s.passed for s in safety_subs.values())
        metrics.append(
            AxisMetric(
                name=SAFETY_AXIS,
                baseline=0.0,
                candidate=0.0,
                delta=0.0,
                ci_low=None,
                ci_high=None,
                significant=False,
                passed=not sub_regressed,
                sub_metrics=safety_subs,
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
    if not isinstance(rec, dict):
        return None
    breakdown = rec.get("axis_breakdown")
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
