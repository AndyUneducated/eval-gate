"""GateReport surfaces nested sub-metric axes under both ``quality`` (Phase 8/9)
and ``safety`` (Phase 10) when records carry ``axis_breakdown``."""

from __future__ import annotations

from evalgate.gate.decision import build_gate_report


def _record(
    score: float,
    *,
    quality_metrics: dict[str, float] | None = None,
    safety_metrics: dict[str, float] | None = None,
    safety_violation: bool = False,
) -> dict:
    breakdown: dict[str, dict[str, float]] = {}
    if quality_metrics is not None:
        breakdown["quality"] = quality_metrics
    if safety_metrics is not None:
        breakdown["safety"] = safety_metrics
    return {
        "case_id": "x",
        "tags": [],
        "score": score,
        "cost_usd": 0.0,
        "latency_ms": 100,
        "safety_violation": safety_violation,
        "axis_breakdown": breakdown or None,
    }


def test_no_axis_breakdown_means_no_quality_or_safety_subaxes():
    baseline = [_record(0.9) for _ in range(10)]
    candidate = [_record(0.85) for _ in range(10)]
    report = build_gate_report(baseline, candidate)
    quality = next(a for a in report.axes if a.name == "quality")
    safety = next(a for a in report.axes if a.name == "safety")
    assert quality.sub_metrics is None
    assert safety.sub_metrics is None


def test_quality_breakdown_becomes_nested_axes_under_quality():
    metrics = {"faithfulness": 0.9, "context_precision": 0.8, "answer_relevance": 1.0}
    baseline = [_record(0.9, quality_metrics=metrics) for _ in range(20)]
    candidate = [_record(0.85, quality_metrics=metrics) for _ in range(20)]
    report = build_gate_report(baseline, candidate)
    quality = next(a for a in report.axes if a.name == "quality")
    assert quality.sub_metrics is not None
    assert set(quality.sub_metrics) == {"faithfulness", "context_precision", "answer_relevance"}
    for sub in quality.sub_metrics.values():
        assert sub.passed is True
        assert sub.delta == 0.0


def test_significantly_worse_quality_sub_metric_fails_quality_axis():
    baseline = [
        _record(
            0.85,
            quality_metrics={
                "faithfulness": 0.95,
                "context_precision": 0.8,
                "answer_relevance": 0.8,
            },
        )
        for _ in range(30)
    ]
    candidate = [
        _record(
            0.85,
            quality_metrics={
                "faithfulness": 0.05,
                "context_precision": 0.95,
                "answer_relevance": 0.85,
            },
        )
        for _ in range(30)
    ]
    report = build_gate_report(baseline, candidate)
    quality = next(a for a in report.axes if a.name == "quality")
    assert quality.sub_metrics is not None
    faith = quality.sub_metrics["faithfulness"]
    assert faith.passed is False
    assert quality.passed is False
    assert report.passed is False
    assert "faithfulness" in (report.summary or "")


def test_only_subset_of_records_with_quality_breakdown_still_aggregates():
    baseline = [_record(0.9) for _ in range(10)] + [
        _record(0.9, quality_metrics={"faithfulness": 0.9}) for _ in range(10)
    ]
    candidate = [_record(0.9) for _ in range(10)] + [
        _record(0.9, quality_metrics={"faithfulness": 0.9}) for _ in range(10)
    ]
    report = build_gate_report(baseline, candidate)
    quality = next(a for a in report.axes if a.name == "quality")
    assert quality.sub_metrics is not None
    assert "faithfulness" in quality.sub_metrics
    assert quality.sub_metrics["faithfulness"].baseline == 0.9


def test_agent_quality_submetrics_are_reported_under_quality():
    baseline = [
        _record(1.0, quality_metrics={"tool_call_accuracy": 1.0, "step_wise_success": 1.0})
        for _ in range(20)
    ]
    candidate = [
        _record(0.5, quality_metrics={"tool_call_accuracy": 0.5, "step_wise_success": 0.5})
        for _ in range(20)
    ]
    report = build_gate_report(baseline, candidate)
    quality = next(a for a in report.axes if a.name == "quality")
    assert quality.sub_metrics is not None
    assert set(quality.sub_metrics) == {"tool_call_accuracy", "step_wise_success"}
    assert quality.sub_metrics["tool_call_accuracy"].candidate == 0.5
    assert quality.sub_metrics["step_wise_success"].candidate == 0.5


def test_safety_breakdown_becomes_nested_axes_under_safety():
    """Phase 10: safety axis exposes its 4 sub-metrics, lower_is_better."""

    clean = {
        "pii_input_rate": 0.0,
        "pii_output_leak_rate": 0.0,
        "jailbreak_attempt_rate": 0.0,
        "jailbreak_compliance_rate": 0.0,
    }
    leaky = {
        "pii_input_rate": 1.0,
        "pii_output_leak_rate": 1.0,
        "jailbreak_attempt_rate": 0.0,
        "jailbreak_compliance_rate": 0.0,
    }
    baseline = [_record(0.9, safety_metrics=clean) for _ in range(30)]
    candidate = [_record(0.9, safety_metrics=leaky, safety_violation=True) for _ in range(30)]
    report = build_gate_report(baseline, candidate)
    safety = next(a for a in report.axes if a.name == "safety")
    assert safety.sub_metrics is not None
    assert set(safety.sub_metrics) == {
        "pii_input_rate",
        "pii_output_leak_rate",
        "jailbreak_attempt_rate",
        "jailbreak_compliance_rate",
    }
    leak_axis = safety.sub_metrics["pii_output_leak_rate"]
    # candidate worse on a lower-is-better axis -> regressed
    assert leak_axis.delta == 1.0
    assert leak_axis.passed is False
    assert safety.passed is False
    # the gate summary should call out the regressed safety sub-metric
    assert "pii_output_leak_rate" in (report.summary or "")


def test_safety_subaxes_pass_when_clean_on_both_sides():
    clean = {
        "pii_input_rate": 0.0,
        "pii_output_leak_rate": 0.0,
        "jailbreak_attempt_rate": 0.0,
        "jailbreak_compliance_rate": 0.0,
    }
    baseline = [_record(0.9, safety_metrics=clean) for _ in range(10)]
    candidate = [_record(0.9, safety_metrics=clean) for _ in range(10)]
    report = build_gate_report(baseline, candidate)
    safety = next(a for a in report.axes if a.name == "safety")
    assert safety.sub_metrics is not None
    for sub in safety.sub_metrics.values():
        assert sub.passed is True
    assert safety.passed is True
