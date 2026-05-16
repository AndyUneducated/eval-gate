"""Reports page — pick two runs over the same set, render the gate report."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from evalgate.core.schemas import AxisMetric, GateReport
from evalgate.ui.api_client import EvalGateAPIError, EvalGateClient
from evalgate.ui.format import (
    AXIS_DIRECTION,
    DELTA_COLOR_IMPROVEMENT,
    DELTA_COLOR_REGRESSION,
    axis_status_emoji,
    delta_css,
    format_run_label,
    humanize_cost_usd,
    humanize_latency_ms,
    humanize_score,
    per_case_axis_deltas,
    per_tag_axis_deltas,
    top_regressed_cases,
)
from evalgate.ui.layout import action_bar, page_intro

# How many rows to show per failure-detail table. 5 is enough to surface the
# pattern without blowing up the page for big eval sets.
_FAILURE_DETAIL_TOP_N = 5

# Full per-tag tables at the bottom of the report (all gate axes).
_TAG_ATTRIBUTION_AXES = ("quality", "cost", "latency_p95", "safety")
_TAG_ATTRIBUTION_LABELS: dict[str, str] = {
    "quality": "Quality (mean score)",
    "cost": "Cost (mean USD per case)",
    "latency_p95": "Latency (mean ms per case in tag)",
    "safety": "Safety (violation rate)",
}


def _format_axis_value(axis_name: str, value: float) -> str:
    """Render a per-axis value using the same units the gate uses."""
    if axis_name == "cost":
        return humanize_cost_usd(value)
    if axis_name == "latency_p95":
        return humanize_latency_ms(value)
    if axis_name == "safety":
        # safety per-case value is 0/1; per-tag value is a rate.
        return f"{value:.2%}" if 0.0 <= value <= 1.0 else f"{value:.4f}"
    return humanize_score(value)


def _format_axis_delta(axis_name: str, delta: float) -> str:
    sign = "+" if delta >= 0 else "-"
    body = _format_axis_value(axis_name, abs(delta))
    return f"{sign}{body}"


def _axis_card(axis) -> None:
    direction = AXIS_DIRECTION.get(axis.name, "higher_is_better")
    higher_is_worse = direction == "lower_is_better"
    delta_str = f"{axis.delta:+.4f}"
    label = f"{axis.name}  ({axis_status_emoji(axis.passed)})"
    st.metric(
        label=label,
        value=f"{axis.candidate:.4f}",
        delta=delta_str,
        delta_color="inverse" if higher_is_worse else "normal",
    )
    st.caption(
        f"baseline={axis.baseline:.4f} · ci=[{axis.ci_low or 0:.3f}, {axis.ci_high or 0:.3f}] "
        f"· significant={axis.significant}"
    )

    if axis.sub_metrics:
        with st.expander(f"{axis.name} sub-axes ({len(axis.sub_metrics)})", expanded=True):
            st.dataframe(
                [
                    {
                        "metric": name,
                        "baseline": f"{sub.baseline:.4f}",
                        "candidate": f"{sub.candidate:.4f}",
                        "delta": f"{sub.delta:+.4f}",
                        "passed": axis_status_emoji(sub.passed),
                    }
                    for name, sub in axis.sub_metrics.items()
                ],
                use_container_width=True,
                hide_index=True,
            )


def _render_failure_details(
    axis: AxisMetric,
    baseline_records: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
) -> None:
    """Drill-down shown under any FAILed axis.

    Three rows in priority order — none of them open unless they have
    content, so passing axes (which never reach here) stay invisible and
    failing-but-data-thin axes don't render empty placeholders.

    1. Regressed sub-metrics — when ``axis.sub_metrics`` carries the
       breakdown that *caused* the fail (RAG ragas / safety sub-rates).
    2. Top regressed tags — only tags that moved in the bad direction
       (top N). The full per-tag tables for every axis live in
       **Tag attribution** below.
    3. Top regressed cases — paired by ``case_id`` and sorted worst-first.
    """
    st.markdown(f"##### Why **{axis.name}** failed")
    direction = AXIS_DIRECTION.get(axis.name, "higher_is_better")

    if axis.sub_metrics:
        regressed_subs = {name: sub for name, sub in axis.sub_metrics.items() if not sub.passed}
        if regressed_subs:
            st.caption("Regressed sub-metrics")
            sub_raw_deltas = [sub.delta for sub in regressed_subs.values()]
            sub_df = pd.DataFrame(
                [
                    {
                        "metric": name,
                        "baseline": f"{sub.baseline:.4f}",
                        "candidate": f"{sub.candidate:.4f}",
                        "delta": f"{sub.delta:+.4f}",
                        "significant": sub.significant,
                    }
                    for name, sub in regressed_subs.items()
                ]
            )
            sub_styled = sub_df.style.apply(
                lambda _col: [delta_css(d, axis.name) for d in sub_raw_deltas],
                subset=["delta"],
            )
            st.dataframe(sub_styled, use_container_width=True, hide_index=True)

    tag_rows = per_tag_axis_deltas(axis.name, baseline_records, candidate_records)
    # Only show tag rows that actually moved in the regression direction —
    # listing every tag for a failure means it's not really tag-localized.
    bad_sign = -1.0 if direction == "higher_is_better" else 1.0
    bad_tag_rows = [r for r in tag_rows if r["delta"] * bad_sign > 0][:_FAILURE_DETAIL_TOP_N]
    if bad_tag_rows:
        st.caption("Top regressed tags")
        tag_raw_deltas = [r["delta"] for r in bad_tag_rows]
        tag_df = pd.DataFrame(
            [
                {
                    "tag": r["tag"],
                    "baseline": _format_axis_value(axis.name, r["baseline"]),
                    "candidate": _format_axis_value(axis.name, r["candidate"]),
                    "delta": _format_axis_delta(axis.name, r["delta"]),
                    "cases (b / c)": f"{r['n_baseline']} / {r['n_candidate']}",
                }
                for r in bad_tag_rows
            ]
        )
        tag_styled = tag_df.style.apply(
            lambda _col: [delta_css(d, axis.name) for d in tag_raw_deltas],
            subset=["delta"],
        )
        st.dataframe(tag_styled, use_container_width=True, hide_index=True)

    case_rows = per_case_axis_deltas(axis.name, baseline_records, candidate_records)
    worst_cases = top_regressed_cases(
        case_rows,
        axis_name=axis.name,
        n=_FAILURE_DETAIL_TOP_N,
    )
    # Same regression-only filter as tags. Top-N already chose worst-first.
    worst_cases = [r for r in worst_cases if r["delta"] * bad_sign > 0]
    if worst_cases:
        st.caption(f"Top {_FAILURE_DETAIL_TOP_N} regressed cases")
        case_raw_deltas = [r["delta"] for r in worst_cases]
        case_df = pd.DataFrame(
            [
                {
                    "case_id": r["case_id"][:8],
                    "tags": ", ".join(r["tags"]) or "—",
                    "baseline": _format_axis_value(axis.name, r["baseline"]),
                    "candidate": _format_axis_value(axis.name, r["candidate"]),
                    "delta": _format_axis_delta(axis.name, r["delta"]),
                }
                for r in worst_cases
            ]
        )
        case_styled = case_df.style.apply(
            lambda _col: [delta_css(d, axis.name) for d in case_raw_deltas],
            subset=["delta"],
        )
        st.dataframe(case_styled, use_container_width=True, hide_index=True)

    if not (axis.sub_metrics or bad_tag_rows or worst_cases):
        st.caption(
            "No per-tag or per-case signal — fail is from the aggregate alone. "
            "Check the run records on **Eval Sets** for context."
        )


def _render_verdict_header(report: GateReport) -> None:
    """Page-level pass/fail headline.

    Axis-level detail lives in the metric row and Failure details below —
    the header only states the overall gate outcome.
    """
    if report.passed:
        st.markdown(
            f"## <span style='color:{DELTA_COLOR_IMPROVEMENT}'>Verdict: PASS</span>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"## <span style='color:{DELTA_COLOR_REGRESSION}'>Verdict: FAIL</span>",
            unsafe_allow_html=True,
        )


def _render_summary_block(summary: str) -> None:
    """Render the gate summary as a tidy bulleted info box.

    The server emits a period-separated paragraph. We keep the wording but
    split on `". "` so each clause sits on its own line inside an info box —
    same content, easier to scan than a wall of text.
    """
    if not summary:
        return
    parts = [p.strip().rstrip(".") for p in summary.split(". ") if p.strip()]
    if not parts:
        return
    bullet_md = "\n".join(f"- {p}" for p in parts)
    st.info(bullet_md)


def _render_run_meta_row(baseline_run: dict[str, Any], candidate_run: dict[str, Any]) -> None:
    """Show one line of run metadata for baseline vs candidate.

    Surfaces fields the run-picker label doesn't fit: ``prompt_hash[:8]``,
    ``judge_model``, ``total_cases``. Two columns side-by-side mirror the
    baseline / candidate picker layout above.
    """

    def _one(label: str, run: dict[str, Any]) -> None:
        with st.container(border=True):
            st.caption(label)
            prompt = run.get("prompt_path") or "?"
            phash = (run.get("prompt_hash") or "")[:8] or "?"
            model = run.get("candidate_model") or "?"
            judge = run.get("judge_model") or "?"
            n = run.get("total_cases", "?")
            score = run.get("mean_score")
            score_str = humanize_score(score) if score is not None else "—"
            st.markdown(
                f"**prompt** `{prompt}` · **hash** `{phash}`  \n"
                f"**candidate** `{model}` · **judge** `{judge}`  \n"
                f"**cases** {n} · **mean score** {score_str}"
            )

    col_a, col_b = st.columns(2)
    with col_a:
        _one("Baseline run", baseline_run)
    with col_b:
        _one("Candidate run", candidate_run)


def _render_tag_attribution_table(axis_name: str, tag_rows: list[dict[str, Any]]) -> None:
    """One axis slice of the full per-tag attribution block — always uses progress bars.

    ``min_value`` is 0; ``max_value`` is derived from the data so the bars
    scale correctly for axes with unbounded ranges (cost in USD, latency in ms).
    Quality and safety are naturally 0-1. Delta stays as formatted text so
    units (``ms``, ``$``, ``%``) are visible.
    """
    if not tag_rows:
        return

    # Fixed 0-1 scale for quality / safety; data-driven for cost / latency.
    if axis_name in ("quality", "safety"):
        max_val = 1.0
    else:
        all_vals = [r["baseline"] for r in tag_rows] + [r["candidate"] for r in tag_rows]
        max_val = max(all_vals) if all_vals else 1.0
        if max_val == 0:
            max_val = 1.0

    fmt = {
        "quality": "%.3f",
        "safety": "%.3f",
        "cost": "%.4f",
        "latency_p95": "%.0f",
    }.get(axis_name, "%.3f")

    delta_help = (
        "candidate - baseline (positive = regression)"
        if AXIS_DIRECTION.get(axis_name, "higher_is_better") == "lower_is_better"
        else "candidate - baseline (negative = regression)"
    )

    raw_deltas = [r["delta"] for r in tag_rows]
    display_rows = [
        {
            "tag": r["tag"],
            "baseline": r["baseline"],
            "candidate": r["candidate"],
            "delta": _format_axis_delta(axis_name, r["delta"]),
            "cases (b / c)": f"{r['n_baseline']} / {r['n_candidate']}",
        }
        for r in tag_rows
    ]
    df = pd.DataFrame(display_rows)
    styled = df.style.apply(
        lambda _col: [delta_css(d, axis_name) for d in raw_deltas],
        subset=["delta"],
    )
    st.dataframe(
        styled,
        use_container_width=True,
        hide_index=True,
        column_config={
            "tag": st.column_config.TextColumn("tag"),
            "baseline": st.column_config.ProgressColumn(
                "baseline",
                min_value=0.0,
                max_value=max_val,
                format=fmt,
            ),
            "candidate": st.column_config.ProgressColumn(
                "candidate",
                min_value=0.0,
                max_value=1.0 if axis_name in ("quality", "safety") else max_val,
                format=fmt,
            ),
            "delta": st.column_config.TextColumn("delta", help=delta_help),
            "cases (b / c)": st.column_config.TextColumn("cases (b / c)"),
        },
    )


def _render_tag_attribution(
    baseline_records: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
) -> None:
    """Full per-tag breakdown for every gate axis (PASS or FAIL).

    Unlike **Failure details**, this lists *all* tags and does not drill into
    individual cases. Latency per tag is the mean ``latency_ms`` in that tag,
    not the run-level p95 on the gate card.
    """
    sections: list[tuple[str, list[dict[str, Any]]]] = []
    for axis_name in _TAG_ATTRIBUTION_AXES:
        rows = per_tag_axis_deltas(axis_name, baseline_records, candidate_records)
        if rows:
            sections.append((axis_name, rows))

    if not sections:
        return

    st.divider()
    st.subheader(
        "Tag attribution",
        help=(
            "Per-tag means for each gate axis — full slice overview. "
            "When an axis fails, **Failure details** above shows only the worst "
            "regressed tags and cases for that axis."
        ),
    )
    st.caption(
        "Gate cards use run-level aggregates (e.g. p95 latency); per-tag latency "
        "here is the mean ms of cases carrying that tag."
    )

    for axis_name, rows in sections:
        label = _TAG_ATTRIBUTION_LABELS.get(axis_name, axis_name)
        st.markdown(f"##### {label}")
        _render_tag_attribution_table(axis_name, rows)


def _render_report(
    report: GateReport,
    baseline_records: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
) -> None:
    _render_verdict_header(report)

    cols = st.columns(len(report.axes))
    for col, axis in zip(cols, report.axes, strict=False):
        with col:
            _axis_card(axis)

    failed_axes = [axis for axis in report.axes if not axis.passed]
    if failed_axes:
        st.divider()
        st.subheader("Failure details")
        _render_summary_block(report.summary or "")
        for axis in failed_axes:
            with st.container(border=True):
                _render_failure_details(axis, baseline_records, candidate_records)
    elif report.summary:
        st.divider()
        _render_summary_block(report.summary)

    _render_tag_attribution(baseline_records, candidate_records)


def main() -> None:
    st.set_page_config(page_title="Reports · EvalGate", layout="wide")

    page_intro(
        "Gate Reports",
        "Compare two eval runs over the same set (baseline vs candidate) and "
        "render the four-axis gate with sub-axis breakdown and tag attribution.",
    )

    with EvalGateClient() as client:
        try:
            sets = client.list_eval_sets(limit=200)
        except EvalGateAPIError as exc:
            st.error(f"Failed to list eval sets: {exc.detail}")
            return

        if not sets:
            st.info("No eval sets — create one on the **Eval Sets** page first.")
            return

        labels = {f"{s.name} ({s.id[:8]})": s.id for s in sets}

        with action_bar():
            set_col, baseline_col, candidate_col, run_col = st.columns(
                [2, 2, 2, 1],
                gap="medium",
                vertical_alignment="bottom",
            )
            with set_col:
                picked_label = st.selectbox(
                    "Eval set",
                    options=list(labels.keys()),
                    help="Runs listed below belong to this set.",
                )
            eval_set_id = labels[picked_label]

            try:
                runs = client.list_runs(eval_set_id=eval_set_id, limit=50)
            except EvalGateAPIError as exc:
                st.error(f"Failed to list runs: {exc.detail}")
                return

            if len(runs) < 2:
                st.warning(
                    "Need at least two `eval_runs` over this set. "
                    "Run `evalgate run` twice (baseline, then candidate)."
                )
                return

            runs_by_id: dict[str, dict[str, Any]] = {r["id"]: r for r in runs}
            run_labels = {format_run_label(r): r["id"] for r in runs}
            run_options = list(run_labels.keys())

            with baseline_col:
                baseline_label = st.selectbox(
                    "Baseline run",
                    options=run_options,
                    index=min(1, len(run_options) - 1),
                    help="Reference run for the gate comparison.",
                )
            with candidate_col:
                candidate_label = st.selectbox(
                    "Candidate run",
                    options=run_options,
                    index=0,
                    help="Run under test — regressions are measured against baseline.",
                )
            with run_col:
                run_clicked = st.button(
                    "Run gate",
                    type="primary",
                    use_container_width=True,
                )

        baseline_id = run_labels[baseline_label]
        candidate_id = run_labels[candidate_label]

        if baseline_id == candidate_id:
            st.warning("Pick two different runs.")
            return

        if not run_clicked:
            st.caption("Choose baseline and candidate above, then click **Run gate**.")
            return

        try:
            baseline_records = client.get_run_records(baseline_id)
            candidate_records = client.get_run_records(candidate_id)
            report = client.run_gate(baseline=baseline_records, candidate=candidate_records)
        except EvalGateAPIError as exc:
            st.error(f"Gate run failed ({exc.status_code}): {exc.detail}")
            return

        _render_run_meta_row(runs_by_id.get(baseline_id, {}), runs_by_id.get(candidate_id, {}))
        st.caption(
            f"baseline: {len(baseline_records)} record(s) · "
            f"candidate: {len(candidate_records)} record(s)"
        )
        _render_report(report, baseline_records, candidate_records)

        st.divider()
        st.subheader(
            "Run sanity check",
            help=(
                "Self-check row: simple aggregates recomputed from the same per-case "
                "records that were sent to the gate (not a second verdict). "
                "Mean score aligns with the quality axis; sum cost and avg latency "
                "are for eyeballing only — the gate uses mean cost and p95 latency."
            ),
        )
        col_score_a, col_score_b = st.columns(2)
        with col_score_a:
            mean_b = sum(r.get("score", 0) for r in baseline_records) / max(
                len(baseline_records), 1
            )
            cost_b = sum(r.get("cost_usd", 0) for r in baseline_records)
            lat_b = (
                sum(r.get("latency_ms", 0) for r in baseline_records) / len(baseline_records)
                if baseline_records
                else 0
            )
            st.metric("baseline mean score", humanize_score(mean_b))
            st.caption(
                f"sum cost {humanize_cost_usd(cost_b)} · avg latency {humanize_latency_ms(lat_b)}"
            )
        with col_score_b:
            mean_c = sum(r.get("score", 0) for r in candidate_records) / max(
                len(candidate_records), 1
            )
            cost_c = sum(r.get("cost_usd", 0) for r in candidate_records)
            lat_c = (
                sum(r.get("latency_ms", 0) for r in candidate_records) / len(candidate_records)
                if candidate_records
                else 0
            )
            st.metric("candidate mean score", humanize_score(mean_c))
            st.caption(
                f"sum cost {humanize_cost_usd(cost_c)} · avg latency {humanize_latency_ms(lat_c)}"
            )


main()
