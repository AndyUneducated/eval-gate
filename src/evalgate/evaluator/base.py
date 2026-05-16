"""Evaluator contract (Phase 8).

An ``Evaluator`` takes one ``EvalCaseRow`` and returns an
:class:`EvaluationOutcome`. The outcome carries everything the runner
needs to persist a row in ``eval_results`` + a fan-out of rows in
``eval_judge_calls`` and to yield a gate-ready ``EvalRecord``.

Why this is its own module (vs. an ``__init__.py`` Protocol):

- The runner imports the contract; concrete evaluators (generic / rag)
  implement it. Putting the contract in a separate module keeps the
  import graph one-way and avoids a cycle between
  ``evaluator/__init__.py`` and ``evaluator/runner.py``.
- ``EvaluationOutcome`` is a dataclass (not pydantic) on purpose: it
  never crosses the API boundary; it's an internal control-flow object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from evalgate.db.models import EvalCaseRow
from evalgate.judge.protocol import JudgeCallRecord


class UnsupportedTaskTypeError(LookupError):
    """Raised when a case's ``task_type`` has no registered evaluator.

    Phase 8 registers ``generic`` (always) and ``rag`` (when the prompt
    YAML carries ``retriever:`` + ``rag_evaluator:``); ``agent`` lands in
    Phase 9. The runner converts this into a per-case error record so a
    misconfigured eval set cannot crash a whole run.
    """


@dataclass
class EvaluationOutcome:
    """The complete signal one evaluator produces for one case.

    Field invariants:

    - ``score`` is always in [0, 1] (clamping is the evaluator's job).
    - ``sub_metrics`` is ``None`` for evaluators that don't break the
      score down (generic); RAG fills it with one entry per ragas metric.
    - ``raw_calls`` is the per-LLM-call audit trail that becomes one row
      in ``eval_judge_calls`` each. RAG evaluators emit one record per
      metric (``judge_model = "ragas:<metric_name>"``).
    - ``output_text`` is the candidate's answer; ``retrieved_contexts`` is
      what the retriever surfaced at run time (``None`` for non-RAG).
    - ``error`` + ``error_kind`` short-circuit the persistence path with
      a score=0 row; the runner never re-raises.
    """

    score: float
    output_text: str
    cost_usd: float
    latency_ms: int
    confidence: float | None = None
    sub_metrics: dict[str, float] | None = None
    retrieved_contexts: list[str] | None = None
    raw_calls: list[JudgeCallRecord] = field(default_factory=list)
    judge_raw: dict[str, Any] | None = None
    reason: str | None = None
    safety_violation: bool = False
    error: bool = False
    error_kind: str | None = None


class Evaluator(Protocol):
    """How the runner talks to a per-task-type pipeline."""

    label: str
    """Stable string used for ``EvalRunRow.judge_model`` (e.g.
    ``ollama/qwen2.5:7b+ollama/qwen2.5:7b`` for generic; ``ragas`` for RAG).
    Read-only after construction."""

    async def evaluate(
        self,
        case: EvalCaseRow,
        *,
        mock: bool = False,
    ) -> EvaluationOutcome:
        """Run the full pipeline for one case. Must never raise; convert
        transport / parser failures into ``error=True`` outcomes so a
        single bad case can't poison the whole run."""
        ...
