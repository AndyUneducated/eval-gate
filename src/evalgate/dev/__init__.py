"""Developer-only helpers — not part of the production data path.

Currently exposes:

* :mod:`evalgate.dev.trace_seeder` — build OTLP-JSON envelopes that exercise
  the same ingest path real OTel SDK exports take, so the ops UI can produce
  demo traces without dragging the OTel SDK into the streamlit process.
"""

from evalgate.dev.trace_seeder import (
    TEMPLATES,
    LlmSpanSpec,
    SpanSpec,
    TraceSpec,
    build_otlp_envelope,
)

__all__ = [
    "TEMPLATES",
    "LlmSpanSpec",
    "SpanSpec",
    "TraceSpec",
    "build_otlp_envelope",
]
