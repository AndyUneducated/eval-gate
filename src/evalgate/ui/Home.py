"""Streamlit landing page for the EvalGate ops UI.

Run with `make ui` (or `streamlit run src/evalgate/ui/Home.py`). Streamlit
auto-discovers the sibling ``pages/`` directory and mounts each file as a
nav entry — we only put the API health badge + a usage hint here.
"""

from __future__ import annotations

import streamlit as st

from evalgate.ui.api_client import EvalGateAPIError, EvalGateClient


def _health_badge(client: EvalGateClient) -> None:
    try:
        body = client.healthz()
    except EvalGateAPIError as exc:
        st.error(f"API error at {client.base_url}: {exc.detail}")
        return
    except Exception as exc:
        st.error(f"API unreachable at {client.base_url}: {exc}")
        return
    st.success(f"API OK · v{body.get('version', '?')} @ {client.base_url}")


def main() -> None:
    st.set_page_config(page_title="EvalGate", layout="wide")
    st.title("EvalGate Ops")
    st.caption(
        "Eval-First LLMOps · use the sidebar to browse Traces, manage Eval Sets, "
        "or compare two runs on the Reports page."
    )

    with EvalGateClient() as client:
        _health_badge(client)

    st.markdown(
        """
### How to use

1. **Traces** — browse captured OTel traces and promote a trace into an eval set.
2. **Eval Sets** — see existing sets + their cases, or create a new set.
3. **Reports** — pick two runs over the same eval set (baseline vs candidate)
   and render the four-axis gate with sub-axis (RAG / safety) breakdown and
   tag attribution.
4. **Generate Trace** — seed a demo trace via the OTLP-JSON ingest path so the
   Traces tab has something to look at without wiring up an external OTel app.

> Run `evalgate run` from the CLI to produce eval_runs that show up here.
"""
    )


if __name__ == "__main__":
    main()
