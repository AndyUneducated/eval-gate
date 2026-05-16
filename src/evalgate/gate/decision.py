"""Assemble axis metrics + attribution into a pass/fail GateReport."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from evalgate.core.schemas import AxisMetric, GateReport
from evalgate.report.attribution import tagwise_attribution
from evalgate.report.multi_axis import build_axis_metrics

EvalRecord = dict[str, Any]


def build_gate_report(
    baseline: Sequence[EvalRecord],
    candidate: Sequence[EvalRecord],
) -> GateReport:
    axes = build_axis_metrics(baseline, candidate)
    attribution = tagwise_attribution(baseline, candidate)
    passed = all(axis.passed for axis in axes)
    summary = _summarize(axes, attribution, passed=passed)
    return GateReport(
        passed=passed,
        axes=axes,
        attribution=attribution,
        summary=summary,
    )


def _summarize(
    axes: Sequence[AxisMetric],
    attribution: dict[str, dict[str, float]],
    *,
    passed: bool,
) -> str:
    if passed:
        return "All four axes within tolerance."
    failed = [axis.name for axis in axes if not axis.passed]
    parts = [f"Regressed axes: {', '.join(failed)}."]
    # Phase 8: if quality regressed *because of* a RAG sub-metric, surface it.
    quality = next((a for a in axes if a.name == "quality"), None)
    if quality and quality.sub_metrics and not quality.passed:
        bad_subs = [
            f"{name} (delta={sub.delta:+.3f})"
            for name, sub in quality.sub_metrics.items()
            if not sub.passed
        ]
        if bad_subs:
            parts.append(f"Quality sub-metrics regressed: {', '.join(bad_subs)}.")
    if attribution:
        worst_tag, worst = min(attribution.items(), key=lambda kv: kv[1]["delta"])
        if worst["delta"] < 0:
            parts.append(f"Worst tag: '{worst_tag}' (delta={worst['delta']:+.3f}).")
    return " ".join(parts)
