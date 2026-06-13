"""PointwiseJudge: ``(input, candidate_output) -> (score, reason)``.

This is the original Phase 5 RubricJudge, isolated into its own class. The
rubric text lives in `prompt.yaml` (`judges[].rubric`); we hand it to the
model verbatim followed by INPUT/OUTPUT blocks.

PointwiseJudge does **not** look at any reference output — that's the
PairwiseJudge's job. Keeping the two as separate classes (rather than one
class with a `mode=` flag) makes the call signature self-explanatory at
every call site and lets each parser specialise.
"""

from __future__ import annotations

from typing import Any

from evalgate.judge.prompt_spec import JudgeSpec
from evalgate.judge.protocol import (
    JudgeCallRecord,
    LeafVerdict,
    acompletion_json,
    parse_score,
    stringify,
)

_MOCK_POINTWISE = '{"score": 0.5, "reason": "mock"}'


class PointwiseJudge:
    def __init__(self, spec: JudgeSpec):
        self.spec = spec

    @property
    def model(self) -> str:
        return self.spec.model

    async def score(
        self,
        case_input: Any,
        candidate_output: str,
        reference_output: str | None = None,
        *,
        sub_run_index: int = 0,
        mock: bool = False,
    ) -> LeafVerdict:
        _ = reference_output
        prompt = (
            f"{self.spec.rubric.strip()}\n\n"
            f"INPUT:\n{stringify(case_input)}\n\n"
            f"OUTPUT:\n{candidate_output}\n"
        )
        text, raw = await acompletion_json(
            model=self.spec.model,
            messages=[{"role": "user", "content": prompt}],
            params=self.spec.params,
            mock_response=_MOCK_POINTWISE if mock else None,
        )
        score, reason = parse_score(text)
        call = JudgeCallRecord(
            judge_model=self.spec.model,
            sub_run_index=sub_run_index,
            position=None,
            score=score,
            winner=None,
            reason=reason,
            raw=raw,
        )
        return LeafVerdict(score=score, agreement=None, calls=[call])
