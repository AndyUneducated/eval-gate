"""Build the four-axis CI gate report (quality / cost / latency_p95 / safety).

Each axis specifies:
  - how to extract a per-case scalar from an eval record,
  - how to aggregate it across cases (mean or p95),
  - whether higher or lower is better.

Mean-aggregated axes use a bootstrap CI to decide significance; p95-aggregated
axes use a simple threshold delta (significance bootstrapping for p95 is a
follow-up — its statistical interpretation is different).
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
    AxisSpec(
        name="safety",
        direction="lower_is_better",
        extractor=lambda r: 1.0 if r.get("safety_violation") else 0.0,
        aggregator="mean",
    ),
)


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
            ci_low: float | None = boot.ci_low
            ci_high: float | None = boot.ci_high
            significant = boot.significant
        else:
            ci_low = ci_high = None
            significant = False

        if spec.direction == "higher_is_better":
            regressed = significant and delta < 0
        else:
            regressed = significant and delta > 0

        sub_metrics: dict[str, AxisMetric] | None = None
        sub_regressed = False
        if spec.name == "quality":
            sub_metrics = _build_sub_metric_axes(baseline, candidate)
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
    return metrics


def _build_sub_metric_axes(
    baseline: Sequence[EvalRecord],
    candidate: Sequence[EvalRecord],
) -> dict[str, AxisMetric] | None:
    """Phase 8 RAG: when records carry a ``sub_metrics`` dict, build one
    higher-is-better mean axis per sub-metric and nest them under
    ``quality.sub_metrics``.

    Returns ``None`` when no record (in either side) carries sub-metrics
    so the gate report stays unchanged for generic-only runs.

    Records without a given sub-metric simply don't contribute to it —
    we only aggregate over records where the metric is present. That
    keeps mixed-task eval sets sensible (a generic case alongside a RAG
    case won't pull faithfulness toward zero).
    """
    names: set[str] = set()
    for rec in list(baseline) + list(candidate):
        sm = rec.get("sub_metrics") if isinstance(rec, dict) else None
        if isinstance(sm, dict):
            names.update(str(k) for k in sm)
    if not names:
        return None

    out: dict[str, AxisMetric] = {}
    for name in sorted(names):
        b_vals = _pluck_metric(baseline, name)
        c_vals = _pluck_metric(candidate, name)
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
        regressed = significant and delta < 0  # all RAG metrics: higher is better
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


def _pluck_metric(records: Sequence[EvalRecord], name: str) -> list[float]:
    out: list[float] = []
    for rec in records:
        sm = rec.get("sub_metrics") if isinstance(rec, dict) else None
        if not isinstance(sm, dict):
            continue
        if name in sm:
            try:
                out.append(float(sm[name]))
            except (TypeError, ValueError):
                continue
    return out
