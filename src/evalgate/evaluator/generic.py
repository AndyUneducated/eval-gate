"""Generic-task evaluator: the Phase 5/6 path lifted into the router world.

This module owns *zero* new behaviour. Everything it does is what
``judge.runner.iter_eval`` used to do inline:

1. Render the candidate prompt + call the candidate LLM.
2. Build the MultiJudge stack and score the candidate output.
3. Pack everything into an :class:`EvaluationOutcome`.

The only refactor is moving step 2's stack construction out of the
hot path: we build it once in ``__init__`` so K-shot self-consistency
doesn't pay setup cost per case.
"""

from __future__ import annotations

from typing import Any

from evalgate.db.models import EvalCaseRow
from evalgate.evaluator.base import EvaluationOutcome
from evalgate.judge.candidate import run_candidate
from evalgate.judge.multi_judge import build_judge_stack
from evalgate.judge.prompt_spec import PromptSpec
from evalgate.judge.protocol import stringify


def _reference_text(expected: dict[str, Any] | None) -> str | None:
    """Best-effort: read ``expected.output`` (Phase 3 case_extract convention,
    or ``expected.answer`` from RAG-style cases); fall back to JSON-stringify."""
    if not expected:
        return None
    for key in ("output", "answer"):
        val = expected.get(key)
        if isinstance(val, str) and val.strip():
            return val
        if val is not None:
            return stringify(val)
    return stringify(expected)


def _judge_models_label(spec: PromptSpec) -> str:
    """``+``-joined judge models — preserves the Phase 6 audit string."""
    return "+".join(j.model for j in spec.judges)


class GenericEvaluator:
    """Evaluator for ``task_type=generic`` cases (the Phase 5/6 path)."""

    def __init__(self, spec: PromptSpec):
        self._spec = spec
        self._judge_stack = build_judge_stack(spec)
        self.label = _judge_models_label(spec)

    async def evaluate(
        self,
        case: EvalCaseRow,
        *,
        mock: bool = False,
    ) -> EvaluationOutcome:
        spec = self._spec
        case_input = dict(case.input or {})
        reference = _reference_text(case.expected)
        mode = spec.judge_policy.mode

        if mode == "pairwise" and not reference:
            return EvaluationOutcome(
                score=0.0,
                output_text="",
                cost_usd=0.0,
                latency_ms=0,
                judge_raw={"error": "missing_reference"},
                reason="pairwise mode requires case.expected (skipped)",
                error=True,
                error_kind="missing_reference",
            )

        cand_mock = "mock-candidate-output" if mock else None
        mock_scores_per_judge: list[list[float]] | None = None
        if mock:
            mock_scores_per_judge = [[0.5] * spec.judge_policy.k for _ in spec.judges]

        try:
            candidate = await run_candidate(case_input, spec, mock_response=cand_mock)
            agg = await self._judge_stack.score(
                case_input,
                candidate.text,
                reference,
                mock_scores_per_judge=mock_scores_per_judge,
            )
        except Exception as exc:
            return EvaluationOutcome(
                score=0.0,
                output_text="",
                cost_usd=0.0,
                latency_ms=0,
                judge_raw={"error": f"runner-failure: {exc}"},
                reason=f"runner-failure: {exc}",
                error=True,
                error_kind="runner_failure",
            )

        judge_raw: dict[str, Any] = {
            "votes": agg.votes,
            "per_judge_confidence": agg.per_judge_confidence,
            "mode": mode,
        }
        return EvaluationOutcome(
            score=agg.score,
            output_text=candidate.text,
            cost_usd=candidate.cost_usd,
            latency_ms=candidate.latency_ms,
            confidence=agg.confidence,
            raw_calls=list(agg.raw_calls),
            judge_raw=judge_raw,
        )
