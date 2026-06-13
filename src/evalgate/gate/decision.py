"""Assemble axis metrics + attribution into a pass/fail GateReport."""

from __future__ import annotations

from collections.abc import Sequence

from evalgate.core.schemas import AxisMetric, GateReport, RecordInput
from evalgate.report.attribution import tagwise_attribution
from evalgate.report.multi_axis import build_axis_metrics


def build_gate_report(
    baseline: Sequence[RecordInput],
    candidate: Sequence[RecordInput],
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
        return "All axes within tolerance."
    failed = [axis.name for axis in axes if not axis.passed]
    parts = [f"Regressed axes: {', '.join(failed)}."]
    # Phase 8/10: when an axis regressed *because of* a nested sub-metric,
    # surface it. Today this fires for ``quality`` (RAG ragas / agent
    # trajectory metrics) and ``safety`` (PII / jailbreak rates).
    for axis in axes:
        if not axis.sub_metrics or axis.passed:
            continue
        bad_subs = [
            f"{name} (delta={sub.delta:+.3f})"
            for name, sub in axis.sub_metrics.items()
            if not sub.passed
        ]
        if bad_subs:
            parts.append(f"{axis.name.capitalize()} sub-metrics regressed: {', '.join(bad_subs)}.")
    if attribution:
        worst_tag, worst = min(attribution.items(), key=lambda kv: kv[1]["delta"])
        if worst["delta"] < 0:
            parts.append(f"Worst tag: '{worst_tag}' (delta={worst['delta']:+.3f}).")
    return " ".join(parts)
