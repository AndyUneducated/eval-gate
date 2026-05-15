"""Unit tests for the `case_extract.extract_case_from_trace` heuristic."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest

from evalgate.core.schemas import TaskKind
from evalgate.ingest.case_extract import NoLLMSpanError, extract_case_from_trace


@dataclass
class FakeSpan:
    """In-memory stand-in for `SpanRow` that satisfies `SpanLike`."""

    span_id: str
    kind: str = "other"
    attributes: dict[str, Any] = field(default_factory=dict)
    parent_span_id: str | None = None
    start_time: datetime = field(default_factory=lambda: datetime(2026, 5, 14))
    name: str = "op"


def _llm_span(span_id: str, **extra) -> FakeSpan:
    base_attrs: dict[str, Any] = {
        "gen_ai.system": "openai",
        "gen_ai.prompt": "what is 2+2?",
        "gen_ai.response.content": "four",
    }
    base_attrs.update(extra.pop("attributes", {}))
    return FakeSpan(span_id=span_id, kind="other", attributes=base_attrs, **extra)


def test_extracts_first_llm_span_as_case() -> None:
    base = datetime(2026, 5, 14, 12, 0, 0)
    spans = [
        FakeSpan("root", attributes={"evalgate.tag": "billing"}, start_time=base),
        _llm_span("llm1", start_time=base + timedelta(seconds=1), parent_span_id="root"),
    ]
    case = extract_case_from_trace(spans)
    assert case["task_type"] == "generic"
    assert case["input"] == {"prompt": "what is 2+2?"}
    assert case["expected"] == {"answer": "four"}
    assert case["tags"] == ["billing"]
    assert case["source_span_id"] == "llm1"


def test_task_type_rag_when_retriever_span_present() -> None:
    base = datetime(2026, 5, 14)
    spans = [
        FakeSpan("root", start_time=base),
        FakeSpan(
            "ret",
            attributes={"evalgate.kind": "retriever"},
            start_time=base + timedelta(milliseconds=10),
            parent_span_id="root",
        ),
        _llm_span("llm1", start_time=base + timedelta(milliseconds=20), parent_span_id="root"),
    ]
    case = extract_case_from_trace(spans)
    assert case["task_type"] == TaskKind.rag.value


def test_task_type_agent_when_multiple_tool_spans() -> None:
    base = datetime(2026, 5, 14)
    spans = [
        FakeSpan("root", start_time=base),
        FakeSpan(
            "t1",
            attributes={"evalgate.kind": "tool"},
            start_time=base + timedelta(milliseconds=10),
            parent_span_id="root",
        ),
        FakeSpan(
            "t2",
            attributes={"evalgate.kind": "tool"},
            start_time=base + timedelta(milliseconds=20),
            parent_span_id="root",
        ),
        _llm_span("llm1", start_time=base + timedelta(milliseconds=30), parent_span_id="root"),
    ]
    case = extract_case_from_trace(spans)
    assert case["task_type"] == TaskKind.agent.value


def test_extra_tags_appended_dedup() -> None:
    spans = [
        FakeSpan("root", attributes={"evalgate.tag": "billing"}),
        _llm_span("llm1", parent_span_id="root"),
    ]
    case = extract_case_from_trace(spans, extra_tags=["billing", "regression"])
    assert case["tags"] == ["billing", "regression"]


def test_task_type_override_wins() -> None:
    spans = [
        FakeSpan("root"),
        _llm_span("llm1", parent_span_id="root"),
    ]
    case = extract_case_from_trace(spans, task_type_override=TaskKind.rag)
    assert case["task_type"] == TaskKind.rag.value


def test_input_fallback_when_no_prompt_attribute() -> None:
    spans = [
        FakeSpan(
            "llm1",
            attributes={
                "gen_ai.system": "openai",
                "gen_ai.request.model": "gpt-4o-mini",
                "gen_ai.request.temperature": 0.0,
            },
        )
    ]
    case = extract_case_from_trace(spans)
    assert case["input"] == {
        "gen_ai.request.model": "gpt-4o-mini",
        "gen_ai.request.temperature": 0.0,
    }
    assert case["expected"] is None


def test_no_llm_span_raises() -> None:
    spans = [
        FakeSpan("root"),
        FakeSpan("child", attributes={"http.url": "..."}, parent_span_id="root"),
    ]
    with pytest.raises(NoLLMSpanError):
        extract_case_from_trace(spans)


def test_empty_span_list_raises() -> None:
    with pytest.raises(NoLLMSpanError):
        extract_case_from_trace([])
