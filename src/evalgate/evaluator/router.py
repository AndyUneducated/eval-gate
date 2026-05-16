"""Dispatch a case to its task-type-specific evaluator.

The router is a thin registry — no behaviour, just a lookup. The
construction of *which* evaluators are available for a given prompt
happens in :func:`build_router`, which inspects the YAML to decide
whether the RAG branch is enabled.

Phase 9 will register an ``agent`` evaluator next to ``rag`` here without
touching the runner.
"""

from __future__ import annotations

from evalgate.core.schemas import TaskKind
from evalgate.db.models import EvalCaseRow
from evalgate.evaluator.base import Evaluator, UnsupportedTaskTypeError
from evalgate.evaluator.generic import GenericEvaluator
from evalgate.judge.prompt_spec import PromptSpec


class EvaluatorRouter:
    def __init__(self, registry: dict[TaskKind, Evaluator]):
        if not registry:
            raise ValueError("EvaluatorRouter needs at least one registered evaluator")
        self._registry = dict(registry)

    @property
    def registered(self) -> set[TaskKind]:
        return set(self._registry)

    def for_case(self, case: EvalCaseRow) -> Evaluator:
        try:
            kind = TaskKind(case.task_type)
        except ValueError as exc:
            raise UnsupportedTaskTypeError(
                f"case {case.id}: unknown task_type {case.task_type!r}"
            ) from exc
        evaluator = self._registry.get(kind)
        if evaluator is None:
            raise UnsupportedTaskTypeError(
                f"case {case.id}: no evaluator registered for task_type={kind.value} "
                f"(registered: {sorted(k.value for k in self._registry)})"
            )
        return evaluator

    def label(self) -> str:
        """Joined evaluator labels for ``EvalRunRow.judge_model`` audit.

        Distinct from the per-evaluator label: this is what shows up in
        the run-level row regardless of which evaluators were actually
        invoked. We sort by ``TaskKind`` for determinism.
        """
        ordered = sorted(self._registry.items(), key=lambda kv: kv[0].value)
        return "+".join(ev.label for _, ev in ordered)


def build_router(spec: PromptSpec, *, mock: bool = False) -> EvaluatorRouter:
    """Translate a ``PromptSpec`` into a fully-built router.

    Always registers :class:`GenericEvaluator` (the existing MultiJudge
    path). When the prompt has both ``retriever:`` and ``rag_evaluator:``
    blocks, also registers the RAG evaluator. ``agent`` is intentionally
    not registered — Phase 9.
    """
    registry: dict[TaskKind, Evaluator] = {
        TaskKind.generic: GenericEvaluator(spec),
    }
    if spec.retriever is not None and spec.rag_evaluator is not None:
        # Imported lazily so generic-only runs don't pay for ragas/langchain
        # import time (and so missing optional deps fail loudly only when
        # someone actually tries to run RAG).
        from evalgate.evaluator.rag import RagEvaluator

        registry[TaskKind.rag] = RagEvaluator(spec, mock=mock)
    if spec.agent_runtime is not None:
        from evalgate.evaluator.agent import AgentTrajectoryEvaluator

        registry[TaskKind.agent] = AgentTrajectoryEvaluator(spec, mock=mock)
    return EvaluatorRouter(registry)
