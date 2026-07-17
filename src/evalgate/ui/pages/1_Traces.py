"""Traces page — browse captured OTel traces and promote one into an eval set."""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime, timedelta

import streamlit as st

from evalgate.ui.api_client import EvalGateAPIError, EvalGateClient
from evalgate.ui.format import humanize_datetime
from evalgate.ui.layout import action_bar, page_intro


def _render_span_tree(spans: list[dict]) -> None:
    """Render spans in a parent->children tree using indentation depth."""
    by_parent: dict[str | None, list[dict]] = {}
    for s in spans:
        by_parent.setdefault(s.get("parent_span_id"), []).append(s)

    def _walk(parent: str | None, depth: int) -> None:
        for span in by_parent.get(parent, []):
            indent = "&nbsp;" * (depth * 4)
            # Span name/kind are producer-controlled; escape before embedding in
            # HTML-enabled markdown so a crafted span name can't inject markup.
            name = html.escape(str(span.get("name", "")))
            kind = html.escape(str(span.get("kind", "")))
            sid = html.escape(str(span.get("span_id", ""))[:8])
            label = f"{indent}**{name}** · `{kind}` · {sid}"
            st.markdown(label, unsafe_allow_html=True)
            with st.expander("attributes", expanded=False):
                st.code(json.dumps(span.get("attributes", {}), indent=2))
            _walk(span["span_id"], depth + 1)

    _walk(None, 0)


def main() -> None:
    st.set_page_config(page_title="Traces · EvalGate", layout="wide")

    page_intro(
        "Traces",
        "Browse traces ingested via OTLP. Adjust filters below, then open a row "
        "to inspect spans or promote a trace into an eval set.",
    )

    with action_bar():
        limit_col, service_col, since_col, refresh_col = st.columns(
            [1, 2, 1, 1],
            gap="medium",
            vertical_alignment="bottom",
        )
        with limit_col:
            limit = st.number_input(
                "Limit",
                min_value=10,
                max_value=500,
                value=50,
                step=10,
                help="Max traces to fetch (newest first).",
            )
        with service_col:
            service = st.text_input(
                "Service",
                value="",
                placeholder="e.g. demo-app",
                help="Optional · filter by `service.name`. Leave empty for all.",
            )
        with since_col:
            since_hours = st.number_input(
                "Since (h)",
                min_value=0,
                value=0,
                step=1,
                help="Optional · only traces newer than this many hours.",
            )
        with refresh_col:
            if st.button("Refresh", use_container_width=True):
                st.cache_data.clear()
                st.rerun()

    since_iso: str | None = None
    if since_hours:
        since_iso = (datetime.now(tz=UTC) - timedelta(hours=int(since_hours))).isoformat()

    with EvalGateClient() as client:
        try:
            traces = client.list_traces(
                limit=int(limit),
                service=service.strip() or None,
                since=since_iso,
            )
        except EvalGateAPIError as exc:
            st.error(f"Failed to list traces: {exc.detail}")
            return

        if not traces:
            st.info(
                "No traces yet. Use **Generate Trace** or push OTLP to the API, "
                "then click **Refresh**."
            )
            return

        st.write(f"Showing **{len(traces)}** trace(s).")
        rows = [
            {
                "trace_id": t["trace_id"],
                "service": t.get("service_name") or "—",
                "spans": t.get("span_count", 0),
                "start": humanize_datetime(t.get("start_time")),
            }
            for t in traces
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Trace detail")
        trace_id = st.selectbox(
            "Pick a trace_id",
            options=[t["trace_id"] for t in traces],
        )
        if not trace_id:
            return

        try:
            detail = client.get_trace(trace_id)
        except EvalGateAPIError as exc:
            st.error(f"Failed to load trace: {exc.detail}")
            return

        col_a, col_b = st.columns([1, 1])
        with col_a:
            st.caption(f"service: `{detail.get('service_name') or '—'}`")
            st.caption(f"start: {humanize_datetime(detail.get('start_time'))}")
            st.caption(f"end: {humanize_datetime(detail.get('end_time'))}")
            st.caption(f"spans: {detail.get('span_count', 0)}")
            with st.expander("resource_attributes", expanded=False):
                st.code(json.dumps(detail.get("resource_attributes", {}), indent=2))
        with col_b:
            _render_span_tree(detail.get("spans", []))

        st.divider()
        st.subheader("Promote to eval set")
        try:
            eval_sets = client.list_eval_sets(limit=100)
        except EvalGateAPIError as exc:
            st.error(f"Failed to list eval sets: {exc.detail}")
            return

        if not eval_sets:
            st.warning("No eval sets yet — create one on the **Eval Sets** page first.")
            return

        names = {f"{s.name} ({s.id[:8]})": s.id for s in eval_sets}
        picked = st.selectbox("Target set", options=list(names.keys()))
        tags_raw = st.text_input("Extra tags (comma-separated)", value="")
        if st.button("Promote trace into set"):
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
            try:
                case = client.add_case_from_trace(
                    set_id=names[picked],
                    trace_id=trace_id,
                    tags=tags,
                )
            except EvalGateAPIError as exc:
                st.error(f"Promote failed ({exc.status_code}): {exc.detail}")
            else:
                st.success(f"Created case {case.id} (task_type={case.task_type}).")


main()
