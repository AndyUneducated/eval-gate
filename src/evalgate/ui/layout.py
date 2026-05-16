"""Shared Streamlit layout helpers for the EvalGate ops UI.

Every page follows the same vertical rhythm (common in ops dashboards):

1. ``page_intro`` — title + one-line caption
2. ``action_bar`` — bordered horizontal toolbar (filters / primary actions)
3. Main content — tables, detail panels, reports

Keeping toolbars in the main column (not the sidebar) matches how Datadog,
Grafana, and Linear place list filters: above the data, full width, easy to
scan left-to-right.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import streamlit as st


def page_intro(title: str, caption: str) -> None:
    """Page title and short description."""
    st.title(title)
    st.caption(caption)


@contextmanager
def action_bar() -> Iterator[None]:
    """Yield inside a bordered toolbar container below the page intro."""
    with st.container(border=True):
        yield
