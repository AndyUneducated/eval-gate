"""Phase 8: task-aware evaluator dispatch.

The runner used to be hard-coded to ``run_candidate -> MultiJudge``. With
RAG (Phase 8) and Agent (Phase 9) needing different evaluation pipelines,
the orchestration moves here:

- :class:`Evaluator` is the contract every evaluator implements.
- :class:`EvaluatorRouter` dispatches a case to its ``task_type``-specific
  evaluator.
- :func:`run_eval` / :func:`iter_eval` (in ``runner.py``) drive the loop.

``judge/`` is now the collection of LLM-as-judge primitives (Pointwise,
Pairwise, MultiJudge, ...). Generic cases re-enter that path via
:mod:`evalgate.evaluator.generic`; RAG cases go through
:mod:`evalgate.evaluator.rag`.
"""

from evalgate.evaluator.base import (
    EvaluationOutcome,
    Evaluator,
    UnsupportedTaskTypeError,
)
from evalgate.evaluator.router import EvaluatorRouter, build_router
from evalgate.evaluator.runner import RunResult, iter_eval, run_eval

__all__ = [
    "EvaluationOutcome",
    "Evaluator",
    "EvaluatorRouter",
    "RunResult",
    "UnsupportedTaskTypeError",
    "build_router",
    "iter_eval",
    "run_eval",
]
