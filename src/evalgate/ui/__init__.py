"""Streamlit ops UI for EvalGate.

The UI is a separate process that talks to the FastAPI backend over HTTP
(`/v1/*`) — never directly to the database. This keeps the streamlit
runtime, which has fragile asyncio semantics, away from SQLAlchemy async
sessions and makes the UI a real consumer of the same REST surface that
CLI / CI use.

Entry point: ``streamlit run src/evalgate/ui/Home.py`` (also exposed as
``make ui``).
"""

from evalgate.ui.api_client import EvalGateAPIError, EvalGateClient

__all__ = ["EvalGateAPIError", "EvalGateClient"]
