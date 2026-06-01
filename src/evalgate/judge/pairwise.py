"""PairwiseJudge: ``(input, candidate, reference, position) -> winner``.

Pairwise judging asks "is candidate better than reference, or worse, or a
tie?". The MT-Bench paper (Zheng 2023) showed LLMs prefer whichever answer
comes first; ``PositionSwapJudge`` calls this judge twice with swapped A/B
positions and only trusts the verdict when both runs agree.

We deliberately **do not output a 0..1 score** from PairwiseJudge — that
would conflate the absolute-quality semantics used elsewhere with the
relative-preference semantics here. The swap layer aggregates two winners
into a 0/0.5/1 outcome.

The rubric in `prompt.yaml` is ignored in pairwise mode: a single fixed
template keeps the schema stable across N sub-judges, and pointwise rubric
text often doesn't translate to A/B comparisons anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from evalgate.judge.prompt_spec import JudgeSpec
from evalgate.judge.protocol import (
    JudgeCallRecord,
    acompletion_json,
    parse_winner,
    stringify,
)

Position = Literal["A_FIRST", "B_FIRST"]

_TEMPLATE = """You are an impartial judge comparing two assistant answers.

INPUT:
{input}

Answer A:
{answer_a}

Answer B:
{answer_b}

Pick the better answer. Return STRICT JSON of the form:
{{"winner": "A" | "B" | "tie", "reason": "<one sentence>"}}
"""

_MOCK_PAIRWISE = '{"winner": "tie", "reason": "mock"}'


@dataclass
class PairwiseVerdict:
    winner: str | None  # "A" / "B" / "tie" / None on parse failure
    reason: str
    raw: dict[str, Any]


class PairwiseJudge:
    def __init__(self, spec: JudgeSpec):
        self.spec = spec

    @property
    def model(self) -> str:
        return self.spec.model

    async def compare(
        self,
        case_input: Any,
        candidate_output: str,
        reference_output: str,
        *,
        position: Position = "A_FIRST",
        sub_run_index: int = 0,
        mock: bool = False,
    ) -> tuple[PairwiseVerdict, JudgeCallRecord]:
        if position == "A_FIRST":
            answer_a, answer_b = candidate_output, reference_output
        else:
            answer_a, answer_b = reference_output, candidate_output

        prompt = _TEMPLATE.format(
            input=stringify(case_input),
            answer_a=answer_a,
            answer_b=answer_b,
        )

        text, raw = await acompletion_json(
            model=self.spec.model,
            messages=[{"role": "user", "content": prompt}],
            params=self.spec.params,
            mock_response=_MOCK_PAIRWISE if mock else None,
        )
        winner, reason = parse_winner(text)

        verdict = PairwiseVerdict(winner=winner, reason=reason, raw=raw)
        call = JudgeCallRecord(
            judge_model=self.spec.model,
            sub_run_index=sub_run_index,
            position=position,
            score=None,
            winner=winner,
            reason=reason,
            raw=raw,
        )
        return verdict, call
