from __future__ import annotations

import random
from typing import Any

from evalgate.gate.decision import build_gate_report


def _make_records(seed: int, n: int = 60) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    tags = ("billing", "qa", "general")
    return [
        {
            "case_id": f"c{i}",
            "tags": [rng.choice(tags)],
            "score": round(rng.uniform(0.78, 0.95), 3),
            "cost_usd": round(rng.uniform(0.005, 0.02), 4),
            "latency_ms": rng.randint(800, 1500),
        }
        for i in range(n)
    ]


def test_identical_runs_pass_gate() -> None:
    base = _make_records(seed=0)
    cand = _make_records(seed=0)
    report = build_gate_report(base, cand)
    assert report.passed
    assert all(axis.passed for axis in report.axes)
    assert {a.name for a in report.axes} == {"quality", "cost", "latency_p95"}


def test_quality_regression_fails_gate() -> None:
    base = _make_records(seed=0)
    cand = _make_records(seed=0)
    for r in cand:
        r["score"] = max(0.0, r["score"] - 0.25)
    report = build_gate_report(base, cand)
    assert not report.passed
    quality = next(a for a in report.axes if a.name == "quality")
    assert not quality.passed
    assert quality.delta < 0
    assert quality.significant
    assert "quality" in (report.summary or "")


def test_cost_regression_fails_gate() -> None:
    base = _make_records(seed=0)
    cand = _make_records(seed=0)
    for r in cand:
        r["cost_usd"] = r["cost_usd"] * 2.0 + 0.05
    report = build_gate_report(base, cand)
    cost = next(a for a in report.axes if a.name == "cost")
    assert not cost.passed
    assert cost.delta > 0


def test_attribution_surfaces_worst_tag() -> None:
    base = _make_records(seed=0)
    cand = _make_records(seed=0)
    for r in cand:
        if "billing" in r["tags"]:
            r["score"] = max(0.0, r["score"] - 0.3)
    report = build_gate_report(base, cand)
    assert "billing" in report.attribution
    assert report.attribution["billing"]["delta"] < -0.1
    other_tags = [t for t in report.attribution if t != "billing"]
    assert all(
        abs(report.attribution[t]["delta"]) < abs(report.attribution["billing"]["delta"])
        for t in other_tags
    )


def test_latency_p95_axis_has_bootstrap_ci() -> None:
    base = _make_records(seed=0)
    cand = _make_records(seed=0)
    report = build_gate_report(base, cand)
    latency = next(a for a in report.axes if a.name == "latency_p95")
    # p95 is now judged with the same bootstrap machinery as the mean axes.
    assert latency.ci_low is not None
    assert latency.ci_high is not None
    assert latency.passed


def test_latency_p95_tolerates_sub_band_noise() -> None:
    # A small tail bump (under the relative-tolerance band) must NOT fail the
    # gate, even if the bootstrap CI happens to call it significant.
    base = _make_records(seed=0)
    cand = _make_records(seed=0)
    for r in cand:
        r["latency_ms"] = int(r["latency_ms"] * 1.03)  # +3%, well under the 10% band
    report = build_gate_report(base, cand)
    latency = next(a for a in report.axes if a.name == "latency_p95")
    assert latency.passed
    assert latency.delta > 0


def test_latency_p95_regression_fails_when_tail_worsens() -> None:
    base = _make_records(seed=0)
    cand = _make_records(seed=0)
    for r in cand:
        r["latency_ms"] = 8000
    report = build_gate_report(base, cand)
    latency = next(a for a in report.axes if a.name == "latency_p95")
    assert not latency.passed
    assert latency.delta > 0
    assert latency.significant


def test_error_records_excluded_from_gate() -> None:
    # A case that couldn't be judged (error=True, placeholder score=0.0) must
    # not be counted as a quality-0 regression — it's "no data", not "bad".
    base = _make_records(seed=0)
    cand = _make_records(seed=0)
    cand.append(
        {
            "case_id": "boom",
            "tags": ["billing"],
            "score": 0.0,
            "cost_usd": 0.0,
            "latency_ms": 0,
            "error": True,
            "error_kind": "all_judges_failed",
        }
    )
    report = build_gate_report(base, cand)
    assert report.passed
    quality = next(a for a in report.axes if a.name == "quality")
    assert quality.passed
    # The errored case's 0.0 didn't drag the candidate mean below baseline.
    assert quality.candidate >= 0.75


def test_all_four_axes_can_fail_together() -> None:
    base = _make_records(seed=0)
    cand = _make_records(seed=0)
    clean_safety = {
        "pii_input_rate": 0.0,
        "pii_output_leak_rate": 0.0,
        "jailbreak_attempt_rate": 0.0,
        "jailbreak_compliance_rate": 0.0,
    }
    leaky_safety = {
        "pii_input_rate": 1.0,
        "pii_output_leak_rate": 1.0,
        "jailbreak_attempt_rate": 1.0,
        "jailbreak_compliance_rate": 1.0,
    }
    for r in base:
        r["axis_breakdown"] = {"safety": clean_safety}
    for r in cand:
        r["score"] = 0.45
        r["cost_usd"] = r["cost_usd"] * 4 + 0.05
        r["latency_ms"] = 9000
        r["axis_breakdown"] = {"safety": leaky_safety}
    report = build_gate_report(base, cand)
    assert not report.passed
    failed = {a.name for a in report.axes if not a.passed}
    assert failed == {"quality", "cost", "latency_p95", "safety"}
