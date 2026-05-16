"""Phase 8: GateReport surfaces sub-metric axes under ``quality``."""

from __future__ import annotations

from evalgate.gate.decision import build_gate_report


def _record(score: float, sub_metrics: dict[str, float] | None = None) -> dict:
    return {
        "case_id": "x",
        "tags": [],
        "score": score,
        "cost_usd": 0.0,
        "latency_ms": 100,
        "safety_violation": False,
        "sub_metrics": sub_metrics,
    }


def test_no_sub_metrics_means_no_quality_breakdown():
    baseline = [_record(0.9) for _ in range(10)]
    candidate = [_record(0.85) for _ in range(10)]
    report = build_gate_report(baseline, candidate)
    quality = next(a for a in report.axes if a.name == "quality")
    assert quality.sub_metrics is None


def test_sub_metrics_become_nested_axes_under_quality():
    baseline = [
        _record(0.9, {"faithfulness": 0.9, "context_precision": 0.8, "answer_relevance": 1.0})
        for _ in range(20)
    ]
    candidate = [
        _record(0.85, {"faithfulness": 0.9, "context_precision": 0.8, "answer_relevance": 1.0})
        for _ in range(20)
    ]
    report = build_gate_report(baseline, candidate)
    quality = next(a for a in report.axes if a.name == "quality")
    assert quality.sub_metrics is not None
    assert set(quality.sub_metrics) == {"faithfulness", "context_precision", "answer_relevance"}
    for sub in quality.sub_metrics.values():
        assert sub.passed is True
        assert sub.delta == 0.0


def test_significantly_worse_sub_metric_fails_quality_axis():
    # Baseline has high faithfulness; candidate's faithfulness collapses to 0
    # for every case → bootstrap CI will flag this as significant.
    baseline = [
        _record(0.85, {"faithfulness": 0.95, "context_precision": 0.8, "answer_relevance": 0.8})
        for _ in range(30)
    ]
    candidate = [
        _record(
            0.85,  # composite score unchanged on purpose
            {"faithfulness": 0.05, "context_precision": 0.95, "answer_relevance": 0.85},
        )
        for _ in range(30)
    ]
    report = build_gate_report(baseline, candidate)
    quality = next(a for a in report.axes if a.name == "quality")
    assert quality.sub_metrics is not None
    faith = quality.sub_metrics["faithfulness"]
    assert faith.passed is False
    # The composite quality axis should follow the sub-metric verdict even
    # though its own bootstrap CI sees no change.
    assert quality.passed is False
    assert report.passed is False
    assert "faithfulness" in (report.summary or "")


def test_only_subset_of_records_with_sub_metrics_still_aggregates():
    # Mixed-task eval set: half the cases are generic (no sub_metrics),
    # half are RAG. The faithfulness axis should aggregate over the RAG
    # cases only.
    baseline = [_record(0.9) for _ in range(10)] + [
        _record(0.9, {"faithfulness": 0.9}) for _ in range(10)
    ]
    candidate = [_record(0.9) for _ in range(10)] + [
        _record(0.9, {"faithfulness": 0.9}) for _ in range(10)
    ]
    report = build_gate_report(baseline, candidate)
    quality = next(a for a in report.axes if a.name == "quality")
    assert quality.sub_metrics is not None
    assert "faithfulness" in quality.sub_metrics
    assert quality.sub_metrics["faithfulness"].baseline == 0.9


def test_agent_submetrics_are_reported_under_quality():
    baseline = [
        _record(1.0, {"tool_call_accuracy": 1.0, "step_wise_success": 1.0}) for _ in range(20)
    ]
    candidate = [
        _record(0.5, {"tool_call_accuracy": 0.5, "step_wise_success": 0.5}) for _ in range(20)
    ]
    report = build_gate_report(baseline, candidate)
    quality = next(a for a in report.axes if a.name == "quality")
    assert quality.sub_metrics is not None
    assert set(quality.sub_metrics) == {"tool_call_accuracy", "step_wise_success"}
    assert quality.sub_metrics["tool_call_accuracy"].candidate == 0.5
    assert quality.sub_metrics["step_wise_success"].candidate == 0.5
