"""Generate Trace page — seed a demo trace into the API for the Traces tab.

This page is a thin form over ``POST /v1/dev/seed-trace``. All actual span
construction happens on the API side via
:mod:`evalgate.dev.trace_seeder` — the UI never imports OpenTelemetry. The
form lets the user pick from a handful of templates and then override any
field; every input carries a ``help=`` line in the form
``"required/optional · <what it does>"``.
"""

from __future__ import annotations

import json
from typing import Any

import streamlit as st

from evalgate.dev.trace_seeder import MAX_COUNT, TEMPLATES
from evalgate.ui.api_client import EvalGateAPIError, EvalGateClient
from evalgate.ui.layout import action_bar, page_intro

# Human-readable labels for the template picker (keys stay API-stable).
_TEMPLATE_LABELS: dict[str, str] = {
    "rag": "RAG demo",
    "agent": "Agent demo",
    "safety": "Safety probe",
    "plain": "Plain LLM",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Each tuple: (session_state key, default). Reset on Apply Template; the
# widget `key=` argument seeds itself from session_state automatically.
_FIELD_KEYS: tuple[str, ...] = (
    "gen_service_name",
    "gen_tracer_name",
    "gen_count",
    "gen_root_name",
    "gen_root_kind",
    "gen_root_tag",
    "gen_include_retriever",
    "gen_retriever_name",
    "gen_retriever_k",
    "gen_include_tool",
    "gen_tool_name",
    "gen_tool_attr_name",
    "gen_include_llm",
    "gen_llm_name",
    "gen_llm_system",
    "gen_llm_model",
    "gen_llm_prompt",
    "gen_llm_use_mock",
    "gen_llm_mock_response",
    "gen_extra_attrs_json",
)


def _apply_template(name: str) -> None:
    """Snapshot a TEMPLATES entry into ``st.session_state``.

    Called on Apply. We always overwrite — the user just opted in.
    """
    tpl = TEMPLATES[name]
    root = tpl.root
    retriever = tpl.retriever
    tool = tpl.tool
    llm = tpl.llm

    st.session_state["gen_service_name"] = tpl.service_name
    st.session_state["gen_tracer_name"] = tpl.tracer_name
    st.session_state["gen_count"] = tpl.count

    st.session_state["gen_root_name"] = root.name
    st.session_state["gen_root_kind"] = root.kind
    st.session_state["gen_root_tag"] = str(root.attributes.get("evalgate.tag", ""))

    st.session_state["gen_include_retriever"] = retriever is not None
    st.session_state["gen_retriever_name"] = retriever.name if retriever else "retriever.search"
    st.session_state["gen_retriever_k"] = (
        int(retriever.attributes.get("retriever.k", 3)) if retriever else 3
    )

    st.session_state["gen_include_tool"] = tool is not None
    st.session_state["gen_tool_name"] = tool.name if tool else "tool.web_search"
    st.session_state["gen_tool_attr_name"] = (
        str(tool.attributes.get("tool.name", "web_search")) if tool else "web_search"
    )

    st.session_state["gen_include_llm"] = llm is not None
    if llm is not None:
        st.session_state["gen_llm_name"] = llm.name
        st.session_state["gen_llm_system"] = llm.gen_ai_system
        st.session_state["gen_llm_model"] = llm.gen_ai_model
        st.session_state["gen_llm_prompt"] = llm.prompt
        st.session_state["gen_llm_use_mock"] = llm.use_mock_response
        st.session_state["gen_llm_mock_response"] = llm.mock_response
    else:
        st.session_state.setdefault("gen_llm_name", "llm.call")
        st.session_state.setdefault("gen_llm_system", "openai")
        st.session_state.setdefault("gen_llm_model", "gpt-4o-mini")
        st.session_state.setdefault("gen_llm_prompt", "Say hi.")
        st.session_state.setdefault("gen_llm_use_mock", True)
        st.session_state.setdefault("gen_llm_mock_response", "hi")

    st.session_state["gen_extra_attrs_json"] = (
        json.dumps(tpl.extra_resource_attributes, indent=2)
        if tpl.extra_resource_attributes
        else "{}"
    )


def _ensure_defaults() -> None:
    """Seed first-load defaults from the RAG template."""
    if "gen_service_name" not in st.session_state:
        _apply_template("rag")


def _build_spec_dict() -> tuple[dict[str, Any] | None, str | None]:
    """Materialise the form into a TraceSpec-shaped dict.

    Returns ``(spec, None)`` on success, ``(None, error_message)`` on
    validation failure (raised before we hit the wire).
    """
    raw_extra = st.session_state.get("gen_extra_attrs_json", "{}").strip() or "{}"
    try:
        extra_attrs = json.loads(raw_extra)
    except json.JSONDecodeError as exc:
        return None, f"Extra resource attributes must be valid JSON: {exc.msg}"
    if not isinstance(extra_attrs, dict):
        return None, "Extra resource attributes must be a JSON object."

    service_name = st.session_state["gen_service_name"].strip()
    if not service_name:
        return None, "service.name is required."

    root_attrs: dict[str, Any] = {}
    tag = st.session_state["gen_root_tag"].strip()
    if tag:
        root_attrs["evalgate.tag"] = tag

    spec: dict[str, Any] = {
        "service_name": service_name,
        "tracer_name": st.session_state["gen_tracer_name"].strip() or "evalgate-ui-demo",
        "count": int(st.session_state["gen_count"]),
        "root": {
            "name": st.session_state["gen_root_name"].strip() or "rag-pipeline",
            "kind": st.session_state["gen_root_kind"].strip() or "chain",
            "attributes": root_attrs,
        },
        "extra_resource_attributes": extra_attrs,
    }

    if st.session_state["gen_include_retriever"]:
        spec["retriever"] = {
            "name": st.session_state["gen_retriever_name"].strip() or "retriever.search",
            "kind": "retriever",
            "attributes": {"retriever.k": int(st.session_state["gen_retriever_k"])},
        }
    if st.session_state["gen_include_tool"]:
        spec["tool"] = {
            "name": st.session_state["gen_tool_name"].strip() or "tool.web_search",
            "kind": "tool",
            "attributes": {
                "tool.name": st.session_state["gen_tool_attr_name"].strip() or "web_search",
            },
        }
    if st.session_state["gen_include_llm"]:
        spec["llm"] = {
            "name": st.session_state["gen_llm_name"].strip() or "llm.call",
            "kind": "llm",
            "attributes": {},
            "gen_ai_system": st.session_state["gen_llm_system"].strip() or "openai",
            "gen_ai_model": st.session_state["gen_llm_model"].strip() or "gpt-4o-mini",
            "prompt": st.session_state["gen_llm_prompt"],
            "use_mock_response": bool(st.session_state["gen_llm_use_mock"]),
            "mock_response": st.session_state["gen_llm_mock_response"],
        }

    return spec, None


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="Generate Trace · EvalGate", layout="wide")

    _ensure_defaults()

    page_intro(
        "Generate Trace",
        "Seed a demo trace through the same OTLP-JSON ingest path real OTel "
        "exporters use. The actual span construction happens on the API "
        "(`POST /v1/dev/seed-trace`); this page is just a form.",
    )

    with action_bar():
        pick_col, btn_col = st.columns([5, 1], gap="medium", vertical_alignment="bottom")
        with pick_col:
            template_choice = st.selectbox(
                "Quick template",
                options=list(TEMPLATES.keys()),
                format_func=lambda key: _TEMPLATE_LABELS.get(key, key),
                help=(
                    "optional · load a preset into the fields below. "
                    "You can still edit anything after Apply."
                ),
            )
        with btn_col:
            if st.button("Apply template", use_container_width=True):
                _apply_template(template_choice)
                st.rerun()

    # ---- form ------------------------------------------------------------
    st.subheader("Connection")
    col_a, col_b, col_c = st.columns([2, 2, 1])
    with col_a:
        st.text_input(
            "service.name",
            key="gen_service_name",
            help=(
                "required · OTel resource attribute. Lands in `traces.service_name` "
                "and drives the Traces tab's service filter."
            ),
        )
    with col_b:
        st.text_input(
            "tracer name (scope.name)",
            key="gen_tracer_name",
            help=(
                "optional · OTLP `scope.name`. Cosmetic — not persisted, but "
                "useful to mirror what a real SDK would set."
            ),
        )
    with col_c:
        st.number_input(
            "count",
            key="gen_count",
            min_value=1,
            max_value=MAX_COUNT,
            step=1,
            help=(
                f"optional · how many independent traces to emit per click "
                f"(1..{MAX_COUNT}). Each one gets a fresh `trace_id`."
            ),
        )

    st.subheader("Root span")
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.text_input(
            "name",
            key="gen_root_name",
            help="required · the root span's display name (e.g. `rag-pipeline`).",
        )
    with col_b:
        st.text_input(
            "kind",
            key="gen_root_kind",
            help=(
                "required · evalgate kind hint (`chain` / `tool` / `retriever` / "
                "`llm` / `other`). Stamped as `evalgate.kind` attribute."
            ),
        )
    with col_c:
        st.text_input(
            "evalgate.tag",
            key="gen_root_tag",
            help=(
                "optional · root span attribute used by report tag attribution. "
                "Leave blank to skip."
            ),
        )

    st.subheader("Child spans")

    with st.container(border=True):
        st.checkbox(
            "Include retriever span",
            key="gen_include_retriever",
            help="optional · adds a `retriever` child under the root. RAG-style traces want this.",
        )
        if st.session_state["gen_include_retriever"]:
            r_col_a, r_col_b = st.columns([2, 1])
            with r_col_a:
                st.text_input(
                    "retriever span name",
                    key="gen_retriever_name",
                    help="required · e.g. `retriever.search`.",
                )
            with r_col_b:
                st.number_input(
                    "retriever.k",
                    key="gen_retriever_k",
                    min_value=1,
                    step=1,
                    help="optional · top-K passages, stamped as `retriever.k` attribute.",
                )

    with st.container(border=True):
        st.checkbox(
            "Include tool span",
            key="gen_include_tool",
            help="optional · adds a `tool` child under the root. Use for agent-style traces.",
        )
        if st.session_state["gen_include_tool"]:
            t_col_a, t_col_b = st.columns(2)
            with t_col_a:
                st.text_input(
                    "tool span name",
                    key="gen_tool_name",
                    help="required · e.g. `tool.web_search`.",
                )
            with t_col_b:
                st.text_input(
                    "tool.name attribute",
                    key="gen_tool_attr_name",
                    help="optional · the `tool.name` attribute value (logical tool id).",
                )

    with st.container(border=True):
        st.checkbox(
            "Include LLM span",
            key="gen_include_llm",
            help="optional · adds an `llm` child under the root. Most demo traces want this on.",
        )
        if st.session_state["gen_include_llm"]:
            l_col_a, l_col_b = st.columns(2)
            with l_col_a:
                st.text_input(
                    "llm span name",
                    key="gen_llm_name",
                    help="required · e.g. `llm.call`.",
                )
                st.text_input(
                    "gen_ai.system",
                    key="gen_llm_system",
                    help="optional · provider id (`openai`, `anthropic`, ...). OTel gen_ai semconv.",
                )
                st.text_input(
                    "gen_ai.request.model",
                    key="gen_llm_model",
                    help="optional · model id. Cosmetic in the server seeder; no call is made.",
                )
            with l_col_b:
                st.text_area(
                    "prompt",
                    key="gen_llm_prompt",
                    help=(
                        "optional · stamped as `gen_ai.prompt` attribute on the "
                        "llm span. The server does NOT actually call any model."
                    ),
                    height=120,
                )
                st.checkbox(
                    "use mock response",
                    key="gen_llm_use_mock",
                    help=(
                        "optional · when on, the mock string below lands as "
                        "`gen_ai.response.content`. Turn off to omit the response attr."
                    ),
                )
                st.text_input(
                    "mock_response",
                    key="gen_llm_mock_response",
                    help="optional · only used when `use mock response` is on.",
                )

    with st.expander("Advanced"):
        st.text_area(
            "Extra resource attributes (JSON object)",
            key="gen_extra_attrs_json",
            help=(
                "optional · merged into the OTLP `Resource.attributes` alongside "
                "`service.name`. Must be a JSON object."
            ),
            height=120,
        )

    # ---- submit ----------------------------------------------------------
    st.divider()
    if st.button("Generate", type="primary"):
        spec, err = _build_spec_dict()
        if err:
            st.error(err)
            return

        with EvalGateClient() as client:
            try:
                trace_ids = client.seed_demo_trace(spec)  # type: ignore[arg-type]
            except EvalGateAPIError as exc:
                st.error(f"Seed failed ({exc.status_code}): {exc.detail}")
                return

        if not trace_ids:
            st.warning("Server accepted the request but returned no trace_ids.")
            return

        st.success(f"Generated {len(trace_ids)} trace(s).")
        with st.expander("trace_ids", expanded=True):
            st.code("\n".join(trace_ids))
        st.page_link("pages/1_Traces.py", label="Open Traces tab →")


main()
