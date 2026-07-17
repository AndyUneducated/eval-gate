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
        if isinstance(val, str):
            # An empty / whitespace string is "no reference", not a blank one —
            # keep looking (and don't coerce "" into a bogus reference).
            if val.strip():
                return val
            continue
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

        try:
            candidate = await run_candidate(case_input, spec, mock_response=cand_mock)
            agg = await self._judge_stack.score(
                case_input,
                candidate.text,
                reference,
                mock=mock,
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

        if agg.no_signal:
            # Every judge call failed (e.g. provider outage). Treat as "couldn't
            # be judged" — an error excluded from the mean + gate — not a real 0.0.
            return EvaluationOutcome(
                score=0.0,
                output_text=candidate.text,
                cost_usd=candidate.cost_usd,
                latency_ms=candidate.latency_ms,
                raw_calls=list(agg.raw_calls),
                judge_raw={"error": "all_judges_failed", "mode": mode},
                reason="all judge calls failed (no signal)",
                error=True,
                error_kind="all_judges_failed",
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
