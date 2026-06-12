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
    task_type: TaskKind = TaskKind.generic
    input: dict[str, Any]
    expected: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)
    retrieved_contexts: list[str] = Field(default_factory=list)
    expected_trajectory: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_trace_id: str | None = None
    source_span_id: str | None = None
    created_at: datetime | None = None


class EvalSetOut(BaseModel):
    """API response shape for an eval_set row (without its cases)."""

    id: str
    name: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime


class EvalCaseOut(BaseModel):
    """API response shape for an eval_case row.

    Phase 4.5 made cases set-agnostic at the payload level — a case's
    set membership is expressed by `eval_case_set_memberships`, which is
    returned by `GET /v1/eval-sets/{set_id}` (the set is the container)
    or `POST .../promote` (PromotionOut). The case itself never carries
    `eval_set_id` in API output anymore.
    """

    id: str
    task_type: TaskKind
    input: dict[str, Any]
    expected: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)
    # Phase 8: gold contexts for RAG cases (used as ragas reference_contexts).
    # Empty list for generic / agent cases.
    retrieved_contexts: list[str] = Field(default_factory=list)
    # Phase 9: gold tool plan for agent cases. Every step is
    # {"tool": "...", "args": {...}} and order matters.
    expected_trajectory: list[dict[str, Any]] = Field(default_factory=list)
    source_trace_id: str | None = None
    source_span_id: str | None = None
    created_at: datetime


class EvalSetDetail(EvalSetOut):
    """Set + its cases, returned by `GET /v1/eval-sets/{id}`."""

    cases: list[EvalCaseOut] = Field(default_factory=list)


class JudgeScore(BaseModel):
    case_id: UUID
    judge: str
    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    rationale: str | None = None
    raw: dict[str, Any] | None = None


class EvalRecord(BaseModel):
    """Per-case record produced by `evalgate run` and consumed by `evalgate gate`.

    Field names are part of the public contract — Phase 13 shadow-mode
    `POST /v1/shadow/observe` also consumes this shape, and the gate's
    `multi_axis.AXES` extractors read these keys directly.
    """

    model_config = ConfigDict(extra="allow")

    case_id: str
    tags: list[str] = Field(default_factory=list)
    score: float
    cost_usd: float = 0.0
    latency_ms: int = 0
    # Phase 10 refactor (was Phase 8 ``sub_metrics``): per-axis, per-metric
    # breakdown. Outer key is the gate axis name (``quality`` / ``safety``);
    # inner dict is the per-metric value (RAG: faithfulness/...; safety:
    # pii_input_rate/pii_output_leak_rate/jailbreak_attempt_rate/
    # jailbreak_compliance_rate). ``None`` for plain generic records that
    # don't break the score down. The gate report's matching main axis
    # surfaces a nested sub-axis per inner key when this is populated.
    axis_breakdown: dict[str, dict[str, float]] | None = None


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
    # Phase 8/10: nested per-sub-metric axes. Currently set for ``quality``
    # (from RAG ragas metrics) and ``safety`` (from PII/jailbreak detectors).
    # Each sub-metric is its own mean axis with bootstrap CI; the parent
    # axis ``passed = main passed AND all(sub.passed for sub in sub_metrics)``.
    sub_metrics: dict[str, AxisMetric] | None = None


class GateReport(BaseModel):
    passed: bool
    axes: list[AxisMetric]
    attribution: dict[str, dict[str, float]] = Field(default_factory=dict)
    summary: str | None = None


class ShadowObserveRequest(BaseModel):
    """Phase 13: one shadow observation pushed by the client SDK.

    ``primary`` is the response that was actually served to the user;
    ``candidate`` is the shadow run that was discarded. Both are full
    :class:`EvalRecord`s already scored client-side (SDK-side scoring), so
    the backend stays a thin write + aggregate layer. Observations are
    grouped by ``candidate_prompt_hash`` for the rolling report.
    """

    case_id: str
    tags: list[str] = Field(default_factory=list)
    primary_prompt_hash: str
    candidate_prompt_hash: str
    primary: EvalRecord
    candidate: EvalRecord


class ShadowReportOut(BaseModel):
    """Phase 13: a rolling shadow gate report over a time window.

    ``report`` is the same :class:`GateReport` the PR CI gate produces — the
    rolling shadow aggregation reuses ``build_gate_report`` unchanged, with
    primary records as the baseline and candidate records as the candidate.
    """

    candidate_prompt_hash: str
    window_start: datetime
    window_end: datetime
    n_observations: int
    passed: bool
    report: GateReport


class PromotionOut(BaseModel):
    """API response for `POST /v1/badcases/{eval_result_id}/promote`.

    Mirrors `EvalCaseSetMembershipRow` (Phase 7.5): a structural membership
    pointing the original case at a new set. The case payload itself is
    NOT duplicated — clients that want the full case fetch it via
    `GET /v1/eval-sets/{set_id}` and look for `id == eval_case_id`.
    """

    id: str
    eval_case_id: str
    eval_set_id: str
    promoted_from_result_id: str | None = None
    strategy: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime


class BadCaseOut(BaseModel):
    """API response shape for a single BadCase candidate (Phase 7).

    Mirrors `badcase.finder.BadCase` 1:1. Kept as a separate BaseModel rather
    than reusing the dataclass directly so FastAPI's response_model schema
    stays explicit and clients have a stable shape.
    """

    eval_result_id: str
    eval_case_id: str | None = None
    eval_run_id: str
    score: float
    judge_confidence: float | None = None
    latency_ms: int
    cost_usd: float
    tags: list[str] = Field(default_factory=list)
    strategy: str
    reason: str
    llm_label: dict[str, Any] | None = None
