"""Eval Sets page — create new sets, view existing sets and their cases."""

from __future__ import annotations

import streamlit as st

from evalgate.ui.api_client import EvalGateAPIError, EvalGateClient
from evalgate.ui.format import humanize_datetime
from evalgate.ui.layout import action_bar, page_intro


def main() -> None:
    st.set_page_config(page_title="Eval Sets · EvalGate", layout="wide")

    page_intro(
        "Eval Sets",
        "Group eval cases for runs and gate reports. Create a set below, or "
        "promote traces from the **Traces** tab.",
    )

    with action_bar():
        name_col, desc_col, create_col, refresh_col = st.columns(
            [2, 3, 1, 1],
            gap="medium",
            vertical_alignment="bottom",
        )
        with name_col:
            name = st.text_input(
                "New set name",
                key="es_create_name",
                placeholder="my-eval-set",
                help="Required to create a set.",
            )
        with desc_col:
            desc = st.text_input(
                "Description",
                key="es_create_desc",
                placeholder="Optional",
                help="Short note shown in the list.",
            )
        with create_col:
            create_clicked = st.button("Create", use_container_width=True, type="primary")
        with refresh_col:
            if st.button("Refresh", use_container_width=True):
                st.cache_data.clear()
                st.rerun()

    with EvalGateClient() as client:
        if create_clicked:
            if not name.strip():
                st.warning("Name is required.")
            else:
                try:
                    created = client.create_eval_set(name=name.strip(), description=desc or None)
                except EvalGateAPIError as exc:
                    st.error(f"Create failed ({exc.status_code}): {exc.detail}")
                else:
                    st.success(f"Created `{created.name}` (id `{created.id}`).")

        try:
            sets = client.list_eval_sets(limit=200)
        except EvalGateAPIError as exc:
            st.error(f"Failed to list eval sets: {exc.detail}")
            return

        if not sets:
            st.info("No eval sets yet — enter a name in the toolbar and click **Create**.")
            return

        st.write(f"Showing **{len(sets)}** eval set(s).")
        st.dataframe(
            [
                {
                    "id": s.id[:8],
                    "name": s.name,
                    "description": s.description or "—",
                    "created_at": humanize_datetime(s.created_at),
                }
                for s in sets
            ],
            use_container_width=True,
            hide_index=True,
        )

        st.divider()
        st.subheader("Set detail")

        labels = {f"{s.name} ({s.id[:8]})": s.id for s in sets}
        picked = st.selectbox("Pick a set", options=list(labels.keys()))
        if not picked:
            return

        try:
            detail = client.get_eval_set(labels[picked])
        except EvalGateAPIError as exc:
            st.error(f"Failed to load set: {exc.detail}")
            return

        st.caption(f"id: `{detail.id}` · created {humanize_datetime(detail.created_at)}")
        if detail.description:
            st.caption(detail.description)

        if not detail.cases:
            st.info("No cases in this set yet.")
            return

        st.write(f"**{len(detail.cases)}** case(s):")
        st.dataframe(
            [
                {
                    "id": c.id[:8],
                    "task_type": str(c.task_type),
                    "tags": ", ".join(c.tags) or "—",
                    "source_trace": (c.source_trace_id or "—")[:12],
                    "created_at": humanize_datetime(c.created_at),
                }
                for c in detail.cases
            ],
            use_container_width=True,
            hide_index=True,
        )


main()
