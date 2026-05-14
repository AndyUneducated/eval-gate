"""Internal data model shared across ingest, eval, and gate layers.

Schema is internal — the OTel wire format is mapped into these by `ingest.otel_mapper`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class TaskKind(StrEnum):
    rag = "rag"
    agent = "agent"
    generic = "generic"


class SpanKind(StrEnum):
    llm = "llm"
    tool = "tool"
    chain = "chain"
    retriever = "retriever"
    other = "other"


class Span(BaseModel):
    model_config = ConfigDict(extra="ignore")

    span_id: str
    trace_id: str
    parent_span_id: str | None = None
    name: str
    kind: SpanKind = SpanKind.other
    start_time: datetime
    end_time: datetime
    attributes: dict[str, Any] = Field(default_factory=dict)
    status_code: str = "OK"
    status_message: str | None = None


class Trace(BaseModel):
    trace_id: str
    spans: list[Span]


class EvalCase(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    task_kind: TaskKind = TaskKind.generic
    input: dict[str, Any]
    expected: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class JudgeScore(BaseModel):
    case_id: UUID
    judge: str
    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    rationale: str | None = None
    raw: dict[str, Any] | None = None


class AxisMetric(BaseModel):
    """One axis of the multi-axis CI gate (quality / cost / latency / safety)."""

    name: str
    baseline: float
    candidate: float
    delta: float
    ci_low: float | None = None
    ci_high: float | None = None
    significant: bool = False
    passed: bool = True


class GateReport(BaseModel):
    passed: bool
    axes: list[AxisMetric]
    attribution: dict[str, dict[str, float]] = Field(default_factory=dict)
    summary: str | None = None
