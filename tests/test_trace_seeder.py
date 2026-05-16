"""Unit tests for :mod:`evalgate.dev.trace_seeder`.

These tests run the seeder + the existing OTLP-JSON parser in-process and
inspect the resulting `Span` list. No DB, no HTTP — just the pure
construction path.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from evalgate.dev.trace_seeder import (
    MAX_COUNT,
    TEMPLATES,
    LlmSpanSpec,
    SpanSpec,
    TraceSpec,
    build_otlp_envelope,
)
from evalgate.ingest.otlp import parse_otlp_json


def _parse(spec: TraceSpec):
    envelope = build_otlp_envelope(spec)
    return parse_otlp_json(envelope)


def test_rag_template_produces_three_spans_with_correct_parents() -> None:
    spans, resource_attrs = _parse(TEMPLATES["rag"])

    assert len(spans) == 3
    assert resource_attrs["service.name"] == "demo-app"

    # All three spans share one trace_id.
    trace_ids = {s.trace_id for s in spans}
    assert len(trace_ids) == 1

    root = next(s for s in spans if s.parent_span_id is None)
    children = [s for s in spans if s.parent_span_id == root.span_id]
    assert root.name == "rag-pipeline"
    assert str(root.kind) == "chain"
    assert root.attributes.get("evalgate.tag") == "billing"

    names = sorted(c.name for c in children)
    assert names == ["llm.call", "retriever.search"]


def test_agent_template_emits_tool_and_llm_with_attrs() -> None:
    spans, _ = _parse(TEMPLATES["agent"])

    by_name = {s.name: s for s in spans}
    assert "tool.web_search" in by_name
    assert by_name["tool.web_search"].attributes.get("tool.name") == "web_search"
    assert by_name["llm.call"].attributes.get("gen_ai.system") == "openai"


def test_safety_template_is_root_plus_llm_only() -> None:
    spans, _ = _parse(TEMPLATES["safety"])

    assert len(spans) == 2
    kinds = sorted(str(s.kind) for s in spans)
    assert kinds == ["chain", "llm"]


def test_plain_template_is_minimal() -> None:
    spans, _ = _parse(TEMPLATES["plain"])

    assert len(spans) == 2


def test_disabling_retriever_drops_only_that_span() -> None:
    base = TEMPLATES["rag"].model_copy(update={"retriever": None})
    spans, _ = _parse(base)

    names = sorted(s.name for s in spans)
    assert names == ["llm.call", "rag-pipeline"]


def test_count_greater_than_one_yields_distinct_trace_ids() -> None:
    spec = TEMPLATES["plain"].model_copy(update={"count": 2})
    spans, _ = _parse(spec)

    trace_ids = {s.trace_id for s in spans}
    # 2 traces x 2 spans/trace, each trace has its own id.
    assert len(trace_ids) == 2
    assert len(spans) == 4


def test_extra_resource_attributes_are_merged_into_resource() -> None:
    spec = TraceSpec(
        service_name="custom-service",
        root=SpanSpec(name="root", kind="chain"),
        llm=LlmSpanSpec(name="llm.call"),
        extra_resource_attributes={"deployment.environment": "staging"},
    )
    _, resource_attrs = _parse(spec)
    assert resource_attrs["service.name"] == "custom-service"
    assert resource_attrs["deployment.environment"] == "staging"


def test_llm_prompt_and_mock_response_land_as_gen_ai_attrs() -> None:
    spec = TraceSpec(
        service_name="demo-app",
        root=SpanSpec(name="root", kind="chain"),
        llm=LlmSpanSpec(
            name="llm.call",
            prompt="hello?",
            use_mock_response=True,
            mock_response="hi!",
        ),
    )
    spans, _ = _parse(spec)
    llm = next(s for s in spans if s.name == "llm.call")
    assert llm.attributes["gen_ai.prompt"] == "hello?"
    assert llm.attributes["gen_ai.response.content"] == "hi!"


def test_use_mock_response_off_omits_response_content() -> None:
    spec = TraceSpec(
        service_name="demo-app",
        root=SpanSpec(name="root", kind="chain"),
        llm=LlmSpanSpec(name="llm.call", use_mock_response=False),
    )
    spans, _ = _parse(spec)
    llm = next(s for s in spans if s.name == "llm.call")
    assert "gen_ai.response.content" not in llm.attributes


def test_count_above_max_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TraceSpec(
            service_name="demo-app",
            root=SpanSpec(name="root", kind="chain"),
            count=MAX_COUNT + 1,
        )


def test_empty_service_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TraceSpec(service_name="   ", root=SpanSpec(name="root", kind="chain"))


def test_span_kind_is_taken_from_evalgate_kind_attribute() -> None:
    # `map_otel_span` derives kind from `evalgate.kind` attribute when the OTLP
    # SPAN_KIND enum is unset (which our envelope leaves as 0 -> "other"). The
    # seeder stamps `evalgate.kind`, so children should keep their semantic kind.
    spans, _ = _parse(TEMPLATES["rag"])
    by_name = {s.name: s for s in spans}
    assert str(by_name["retriever.search"].kind) == "retriever"
    assert str(by_name["llm.call"].kind) == "llm"
