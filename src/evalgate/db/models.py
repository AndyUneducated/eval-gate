"""ORM mappings. JSON columns use Postgres JSONB but fall back to plain JSON
for other dialects so tests can run without a Postgres instance.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


JsonType = JSON().with_variant(JSONB(), "postgresql")


class SpanRow(Base):
    __tablename__ = "spans"

    span_id: Mapped[str] = mapped_column(String, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    parent_span_id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="other")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    status_code: Mapped[str] = mapped_column(String, nullable=False, default="OK")
    status_message: Mapped[str | None] = mapped_column(String, nullable=True)


class TraceRow(Base):
    """Per-trace rollup: lets `GET /v1/traces` paginate without scanning spans."""

    __tablename__ = "traces"

    trace_id: Mapped[str] = mapped_column(String, primary_key=True)
    root_span_id: Mapped[str | None] = mapped_column(String, nullable=True)
    service_name: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    span_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resource_attributes: Mapped[dict[str, Any]] = mapped_column(
        JsonType, default=dict, nullable=False
    )


class EvalSetRow(Base):
    """A named collection of eval cases. Cheap to create — one per concern
    (e.g. "billing-regress-cases", "rag-faithfulness")."""

    __tablename__ = "eval_sets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class EvalCaseRow(Base):
    """A single (input, expected) data point — **payload only**.

    Phase 4.5 removed the legacy ``eval_set_id`` column: a case's set
    membership is now expressed exclusively via
    ``EvalCaseSetMembershipRow``. Creating a case and adding it to its
    originating set is two inserts (one ``EvalCaseRow``, one
    ``EvalCaseSetMembershipRow``); promote is just an additional
    membership. Single source of truth.

    ``source_trace_id`` is a *soft* reference (no FK) — eval cases must
    outlive their source trace (which may be archived / pruned by
    retention).
    """

    __tablename__ = "eval_cases"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    task_type: Mapped[str] = mapped_column(String, nullable=False, default="generic")
    input: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    expected: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    tags: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    # Phase 8: gold contexts for RAG cases. Used by ragas as the
    # `reference_contexts` for `context_precision_with_reference` /
    # `context_recall`. For non-RAG cases this stays an empty list.
    retrieved_contexts: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    # Phase 9: gold action plan for agent cases. Every step is
    # {"tool": str, "args": {...}} and order matters.
    # Generic/RAG cases keep this as an empty list.
    expected_trajectory: Mapped[list[dict[str, Any]]] = mapped_column(
        JsonType, default=list, nullable=False
    )
    source_trace_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    source_span_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EvalCaseSetMembershipRow(Base):
    """The *only* place a case's set membership lives (Phase 4.5).

    Every way a case enters a set — ``add_case`` from a manual payload,
    ``add_case_from_trace`` from a captured trace, or ``promote`` from a
    badcase — writes one row here. Promote also fills
    ``promoted_from_result_id`` + ``strategy``; for non-promote inserts
    they stay ``NULL`` (i.e. "originating membership").

    ``promoted_from_result_id`` is a soft reference (no FK) to the
    ``eval_results.id`` that surfaced this case; results may be archived
    independently of memberships.

    The ``(eval_case_id, eval_set_id)`` uniqueness prevents adding the
    same case to the same set twice — a no-op anti-pattern that the API
    surfaces as HTTP 409 (``AlreadyPromotedError``).
    """

    __tablename__ = "eval_case_set_memberships"
    __table_args__ = (
        UniqueConstraint(
            "eval_case_id",
            "eval_set_id",
            name="uq_eval_case_set_memberships_case_set",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    eval_case_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("eval_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    eval_set_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("eval_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Soft ref — the eval_result that triggered this promote, if any.
    promoted_from_result_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # Strategy that surfaced the case (uncertainty / outlier / llm / ...).
    strategy: Mapped[str | None] = mapped_column(String, nullable=True)
    # Extra tags supplied at promotion time. Distinct from ``EvalCaseRow.tags``
    # (which is the case's intrinsic taxonomy) — these are membership-scoped.
    tags: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EvalRunRow(Base):
    """One execution of `evalgate run`: an eval_set x a prompt.yaml snapshot."""

    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    eval_set_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("eval_sets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prompt_path: Mapped[str] = mapped_column(String, nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String, nullable=False)
    candidate_model: Mapped[str] = mapped_column(String, nullable=False)
    judge_model: Mapped[str] = mapped_column(String, nullable=False)
    total_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mean_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EvalResultRow(Base):
    """A single (case, candidate output, judge score) tuple within an EvalRun.

    `eval_case_id` is a *soft* reference (no FK) — results must survive case
    deletion / archival, mirroring `EvalCaseRow.source_trace_id`.
    """

    __tablename__ = "eval_results"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    eval_run_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("eval_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    eval_case_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    tags: Mapped[list[str]] = mapped_column(JsonType, default=list, nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Phase 17 forward-compat: populated later by MultiJudge / calibration.
    judge_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    judge_raw: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    # Phase 10 (was Phase 8 ``sub_metrics``): per-axis, per-metric breakdown.
    # Outer key is a gate axis name (``quality`` / ``safety``); inner dict is
    # the per-metric value. RAG fills ``quality`` with ragas metrics; agent
    # fills ``quality`` with tool_call_accuracy/step_wise_success; the safety
    # pipeline fills ``safety`` with PII / jailbreak rates. ``NULL`` for plain
    # generic results that don't break the score down.
    axis_breakdown: Mapped[dict[str, dict[str, float]] | None] = mapped_column(
        JsonType, nullable=True
    )
    # Phase 8 RAG: contexts the candidate's retriever actually returned at
    # run time. NULL for non-RAG cases. Audit signal for badcase finder.
    retrieved_contexts: Mapped[list[str] | None] = mapped_column(JsonType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EvalJudgeCallRow(Base):
    """One raw judge LLM invocation that contributed to a parent EvalResultRow.

    With N sub-judges x K self-consistency x P position-swap (1 or 2), one
    eval_result can have up to N*K*P rows here. Storing per-call lets Phase
    14 compute Cohen's kappa vs human labels and Phase 17 recompute calibration
    without re-invoking the judge.
    """

    __tablename__ = "eval_judge_calls"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    eval_result_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("eval_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    judge_model: Mapped[str] = mapped_column(String, nullable=False)
    sub_run_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # "A_FIRST" / "B_FIRST" for pairwise; NULL for pointwise calls.
    position: Mapped[str | None] = mapped_column(String, nullable=True)
    # Pointwise: the raw 0..1 score. Pairwise leaf: NULL (only winner is set);
    # PositionSwap-aggregated rows write the 0/0.5/1 outcome here.
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # "A" / "B" / "tie" for pairwise; NULL for pointwise.
    winner: Mapped[str | None] = mapped_column(String, nullable=True)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
