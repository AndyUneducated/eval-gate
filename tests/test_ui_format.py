"""Pure-function tests for `evalgate.ui.format` helpers.

Streamlit pages should not need their own integration tests; instead we
keep all rendering logic that's tricky enough to break (latency units,
attribution sort order, run label) in `format.py` and exercise it here.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from evalgate.ui.format import (
    AXIS_DIRECTION,
    DELTA_COLOR_IMPROVEMENT,
    DELTA_COLOR_NEUTRAL,
    DELTA_COLOR_REGRESSION,
    axis_status_emoji,
    delta_css,
    delta_semantic_kind,
    extract_axis_value,
    format_run_label,
    humanize_cost_usd,
    humanize_datetime,
    humanize_latency_ms,
    humanize_score,
    per_case_axis_deltas,
    per_tag_axis_deltas,
    sort_attribution,
    top_regressed_cases,
)


def test_humanize_latency_ms_under_1s_uses_ms() -> None:
    assert humanize_latency_ms(245) == "245 ms"


def test_humanize_latency_ms_over_1s_uses_seconds() -> None:
    assert humanize_latency_ms(1500) == "1.50 s"


def test_humanize_latency_ms_handles_none() -> None:
    assert humanize_latency_ms(None) == "—"


def test_humanize_cost_usd_zero_is_pinned() -> None:
    assert humanize_cost_usd(0) == "$0.00"


def test_humanize_cost_usd_subcent_uses_4_decimals() -> None:
    assert humanize_cost_usd(0.0021) == "$0.0021"


def test_humanize_cost_usd_supercent_uses_2_decimals() -> None:
    assert humanize_cost_usd(0.95) == "$0.95"


def test_humanize_score_raw_vs_percent() -> None:
    assert humanize_score(0.873) == "0.873"
    assert humanize_score(0.873, percent=True) == "87.3%"
    assert humanize_score(None) == "—"


def test_humanize_datetime_iso_string() -> None:
    out = humanize_datetime("2025-04-15T08:30:00Z")
    assert out == "2025-04-15 08:30"


def test_humanize_datetime_naive_dt_treated_as_utc() -> None:
    naive = datetime(2025, 4, 15, 8, 30)
    assert humanize_datetime(naive) == "2025-04-15 08:30"


def test_humanize_datetime_invalid_string_passes_through() -> None:
    assert humanize_datetime("not-a-date") == "not-a-date"


def test_axis_status_emoji_is_ascii() -> None:
    assert axis_status_emoji(True) == "PASS"
    assert axis_status_emoji(False) == "FAIL"


def test_sort_attribution_lower_is_worse_puts_drops_first() -> None:
    attribution = {
        "billing": {"quality": -0.20, "cost": 0.05},
        "support": {"quality": -0.05},
    }
    out = sort_attribution(attribution, direction="lower_is_worse")
    # The biggest negative quality drop must be first.
    assert out[0] == ("billing", "quality", -0.20)
    # The cost regression (positive delta) sinks to the bottom.
    assert out[-1][2] == 0.05


def test_sort_attribution_higher_is_worse_puts_spikes_first() -> None:
    attribution = {
        "a": {"latency_p95": 0.10},
        "b": {"latency_p95": 0.50},
    }
    out = sort_attribution(attribution, direction="higher_is_worse")
    assert out[0][0] == "b"
    assert out[1][0] == "a"


def test_axis_direction_covers_all_four_gate_axes() -> None:
    # Mirrors evalgate.report.multi_axis.AXES — if the gate adds an axis
    # we want this test to force us to think about its UI direction.
    assert set(AXIS_DIRECTION) == {"quality", "cost", "latency_p95"}
    assert AXIS_DIRECTION["quality"] == "higher_is_better"
    assert AXIS_DIRECTION["cost"] == "lower_is_better"


def test_extract_axis_value_picks_right_field_per_axis() -> None:
    record = {"score": 0.7, "cost_usd": 0.01, "latency_ms": 1200}
    assert extract_axis_value("quality", record) == 0.7
    assert extract_axis_value("cost", record) == 0.01
    assert extract_axis_value("latency_p95", record) == 1200.0


def test_per_case_axis_deltas_pairs_by_case_id_and_drops_unpaired() -> None:
    baseline = [
        {"case_id": "a", "score": 0.9, "tags": ["billing"]},
        {"case_id": "b", "score": 0.8},
    ]
    candidate = [
        {"case_id": "a", "score": 0.7, "tags": ["billing"], "eval_result_id": "r-a"},
        {"case_id": "c", "score": 0.5},  # not in baseline -> dropped
    ]
    rows = per_case_axis_deltas("quality", baseline, candidate)
    assert len(rows) == 1
    row = rows[0]
    assert row["case_id"] == "a"
    assert row["baseline"] == 0.9
    assert row["candidate"] == 0.7
    assert row["delta"] == pytest.approx(-0.2)
    assert row["eval_result_id"] == "r-a"
    assert row["tags"] == ["billing"]


def test_top_regressed_cases_quality_orders_drops_first() -> None:
    rows = [
        {"case_id": "a", "delta": -0.05, "baseline": 0.9, "candidate": 0.85, "tags": []},
        {"case_id": "b", "delta": -0.40, "baseline": 0.9, "candidate": 0.50, "tags": []},
        {"case_id": "c", "delta": +0.10, "baseline": 0.7, "candidate": 0.80, "tags": []},
    ]
    out = top_regressed_cases(rows, axis_name="quality", n=2)
    assert [r["case_id"] for r in out] == ["b", "a"]


def test_top_regressed_cases_cost_orders_spikes_first() -> None:
    rows = [
        {"case_id": "a", "delta": +0.001, "baseline": 0, "candidate": 0, "tags": []},
        {"case_id": "b", "delta": +0.020, "baseline": 0, "candidate": 0, "tags": []},
    ]
    out = top_regressed_cases(rows, axis_name="cost", n=5)
    assert [r["case_id"] for r in out] == ["b", "a"]


def test_per_tag_axis_deltas_quality_picks_worst_tag_first() -> None:
    baseline = [
        {"case_id": "a", "tags": ["billing"], "score": 0.9},
        {"case_id": "b", "tags": ["support"], "score": 0.9},
    ]
    candidate = [
        {"case_id": "a", "tags": ["billing"], "score": 0.6},
        {"case_id": "b", "tags": ["support"], "score": 0.85},
    ]
    out = per_tag_axis_deltas("quality", baseline, candidate)
    assert out[0]["tag"] == "billing"
    assert out[0]["delta"] == pytest.approx(-0.3)
    assert out[-1]["tag"] == "support"


@pytest.mark.parametrize(
    ("delta", "axis_name", "expected"),
    [
        (-0.1, "quality", "regression"),
        (0.1, "quality", "improvement"),
        (0.05, "cost", "regression"),
        (-0.05, "cost", "improvement"),
        (100.0, "latency_p95", "regression"),
        (0.0, "quality", "neutral"),
        (1e-12, "cost", "neutral"),
    ],
)
def test_delta_semantic_kind_respects_axis_direction(
    delta: float, axis_name: str, expected: str
) -> None:
    assert delta_semantic_kind(delta, axis_name) == expected


def test_delta_css_regression_is_red_and_bold() -> None:
    css = delta_css(-0.2, "quality")
    assert DELTA_COLOR_REGRESSION in css
    assert "font-weight: 600" in css


def test_delta_css_improvement_is_green_and_bold() -> None:
    css = delta_css(0.2, "quality")
    assert DELTA_COLOR_IMPROVEMENT in css
    assert "font-weight: 600" in css


def test_delta_css_neutral_is_gray_without_bold() -> None:
    css = delta_css(0.0, "cost")
    assert DELTA_COLOR_NEUTRAL in css
    assert "font-weight" not in css


def test_per_tag_axis_deltas_cost_positive_delta_is_regression() -> None:
    baseline = [{"case_id": "a", "tags": ["billing"], "cost_usd": 0.06}]
    candidate = [{"case_id": "a", "tags": ["billing"], "cost_usd": 0.08}]
    out = per_tag_axis_deltas("cost", baseline, candidate)
    assert len(out) == 1
    assert out[0]["delta"] == pytest.approx(0.02)
    assert delta_semantic_kind(out[0]["delta"], "cost") == "regression"


def test_format_run_label_renders_compact_summary() -> None:
    label = format_run_label(
        {
            "created_at": "2025-04-15T08:30:00Z",
            "prompt_path": "examples/safety_demo/prompts/safety_baseline.yaml",
            "candidate_model": "ollama/qwen3.5:9b",
            "mean_score": 0.812,
        }
    )
    assert "2025-04-15 08:30" in label
    assert "safety_baseline.yaml" in label
    assert "ollama/qwen3.5:9b" in label
    assert "score=0.812" in label
