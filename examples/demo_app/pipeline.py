"""Minimal "real" LLM pipeline that pushes OTLP/HTTP traces to a local EvalGate.

Design notes:
* We use **LiteLLM with `mock_response`** so the demo never burns API credits
  and runs offline in CI. Phase 5 swaps the mock for a real judge call with
  zero changes to the surrounding instrumentation.
* The OTel SDK is wired manually here (`TracerProvider` + `OTLPSpanExporter`)
  instead of using auto-instrumentation. That keeps the dependency surface
  tight and makes the resulting spans deterministic for test snapshots.
* The exporter targets ``/v1/otel/traces`` explicitly — the default OTel path
  is ``/v1/traces``, which we keep free for the SDK-less JSON ingest.
"""

from __future__ import annotations

import os

import litellm
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

DEFAULT_ENDPOINT = "http://localhost:8000/v1/otel/traces"


def _configure_otel(endpoint: str) -> TracerProvider:
    resource = Resource.create({"service.name": "demo-app"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    return provider


def run_pipeline() -> str:
    """Run a single fake RAG-ish pipeline and return the model output."""
    tracer = trace.get_tracer("demo-app")
    with tracer.start_as_current_span("rag-pipeline") as root:
        root.set_attribute("evalgate.kind", "chain")
        root.set_attribute("evalgate.tag", "billing")

        with tracer.start_as_current_span("retriever.search") as ret:
            ret.set_attribute("evalgate.kind", "retriever")
            ret.set_attribute("retriever.k", 3)

        with tracer.start_as_current_span("llm.call") as llm:
            llm.set_attribute("evalgate.kind", "llm")
            llm.set_attribute("gen_ai.system", "openai")
            llm.set_attribute("gen_ai.request.model", "gpt-4o-mini")
            response = litellm.completion(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "user",
                        "content": "Reply with the literal string 'four'.",
                    }
                ],
                mock_response="four",
            )
            content = response["choices"][0]["message"]["content"]
            llm.set_attribute("gen_ai.response.content", content)
            return content


def main() -> None:
    endpoint = os.environ.get("EVALGATE_OTLP_ENDPOINT", DEFAULT_ENDPOINT)
    provider = _configure_otel(endpoint)
    try:
        out = run_pipeline()
        print(f"demo-app -> {out!r} (pushed to {endpoint})")
    finally:
        provider.force_flush()
        provider.shutdown()


if __name__ == "__main__":
    main()
