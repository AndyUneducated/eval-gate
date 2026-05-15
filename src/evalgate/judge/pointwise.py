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

from dataclasses import dataclass
from typing import Any

from evalgate.judge.prompt_spec import JudgeSpec
from evalgate.judge.protocol import (
    JudgeCallRecord,
    acompletion_json,
    parse_score,
    stringify,
)


@dataclass
class PointwiseVerdict:
    score: float
    reason: str
    raw: dict[str, Any]


class PointwiseJudge:
    def __init__(self, spec: JudgeSpec):
        self.spec = spec

    async def score(
        self,
        case_input: Any,
        candidate_output: str,
        *,
        sub_run_index: int = 0,
        mock_response: str | None = None,
    ) -> tuple[PointwiseVerdict, JudgeCallRecord]:
        prompt = (
            f"{self.spec.rubric.strip()}\n\n"
            f"INPUT:\n{stringify(case_input)}\n\n"
            f"OUTPUT:\n{candidate_output}\n"
        )
        text, raw = await acompletion_json(
            model=self.spec.model,
            messages=[{"role": "user", "content": prompt}],
            params=self.spec.params,
            mock_response=mock_response,
        )
        score, reason = parse_score(text)
        verdict = PointwiseVerdict(score=score, reason=reason, raw=raw)
        call = JudgeCallRecord(
            judge_model=self.spec.model,
            sub_run_index=sub_run_index,
            position=None,
            score=score,
            winner=None,
            reason=reason,
            raw=raw,
        )
        return verdict, call
