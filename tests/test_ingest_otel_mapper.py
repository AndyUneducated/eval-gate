from __future__ import annotations

import pytest

from evalgate.core.schemas import SpanKind
from evalgate.ingest.otel_mapper import map_otel_span


def test_map_simple_snake_case_span() -> None:
    raw = {
        "span_id": "abc",
        "trace_id": "xyz",
        "name": "chat.completion",
        "kind": "llm",
        "start_time": "2026-05-14T00:00:00+00:00",
        "end_time": "2026-05-14T00:00:01+00:00",
        "attributes": {"gen_ai.system": "openai"},
        "status": {"code": "OK"},
    }
    span = map_otel_span(raw)
    assert span.span_id == "abc"
    assert span.trace_id == "xyz"
    assert span.kind is SpanKind.llm
    assert span.attributes["gen_ai.system"] == "openai"
    assert span.status_code == "OK"


def test_map_otlp_attribute_list_and_nano_timestamps() -> None:
    raw = {
        "span_id": "abc",
        "trace_id": "xyz",
        "name": "chat",
        "start_time_unix_nano": "1747180800000000000",
        "end_time_unix_nano": "1747180801000000000",
        "attributes": [
            {"key": "gen_ai.system", "value": {"string_value": "openai"}},
            {"key": "gen_ai.usage.input_tokens", "value": {"int_value": "120"}},
            {"key": "gen_ai.request.temperature", "value": {"double_value": 0.7}},
            {"key": "evalgate.is_demo", "value": {"bool_value": True}},
        ],
    }
    span = map_otel_span(raw)
    assert span.attributes["gen_ai.system"] == "openai"
    assert span.attributes["gen_ai.usage.input_tokens"] == 120
    assert span.attributes["gen_ai.request.temperature"] == pytest.approx(0.7)
    assert span.attributes["evalgate.is_demo"] is True
    assert span.start_time.year == 2025  # 1747180800 → 2025-05-14 UTC
    assert (span.end_time - span.start_time).total_seconds() == 1.0


def test_missing_ids_raises() -> None:
    with pytest.raises(ValueError, match="span_id"):
        map_otel_span({"name": "no-ids"})


def test_unknown_kind_falls_back_to_other() -> None:
    raw = {
        "span_id": "a",
        "trace_id": "b",
        "kind": "not-a-real-kind",
        "start_time": "2026-05-14T00:00:00+00:00",
        "end_time": "2026-05-14T00:00:01+00:00",
    }
    span = map_otel_span(raw)
    assert span.kind is SpanKind.other
