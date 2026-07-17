"""PositionSwapJudge: removes positional bias from pairwise judging.

Pairwise LLM judges are well-known to favour whichever answer they read
first (the "first-position bias", see Zheng et al. 2023). Mitigation is
mechanical: call the underlying ``PairwiseJudge`` twice with swapped A/B
positions and only trust verdicts that **agree across both orderings**.

Aggregation rules (this layer):
- both orderings prefer candidate -> ``score=1.0, agreement=True``
- both prefer reference                -> ``score=0.0, agreement=True``
- disagree / either is a tie / parse failure -> ``score=0.5, agreement=False``
"""

from __future__ import annotations

from typing import Any

from evalgate.judge.pairwise import PairwiseJudge
from evalgate.judge.protocol import LeafVerdict


class PositionSwapJudge:
    def __init__(self, leaf: PairwiseJudge, *, enabled: bool = True):
        self.leaf = leaf
        self.enabled = enabled

    @property
    def model(self) -> str:
        return self.leaf.model

    async def score(
        self,
        case_input: Any,
        candidate_output: str,
        reference_output: str | None,
        *,
        sub_run_index: int = 0,
        mock: bool = False,
    ) -> LeafVerdict:
        ref = reference_output or ""
        v_a, call_a = await self.leaf.compare(
            case_input,
            candidate_output,
            ref,
            position="A_FIRST",
            sub_run_index=sub_run_index,
            mock=mock,
        )

        calls = [call_a]

        if not self.enabled:
            score = _winner_to_score(v_a.winner, candidate_is="A")
            # A tie is *not* agreement (matches the enabled two-order path,
            # where a tie yields agreement=False).
            return LeafVerdict(
                score=score,
                agreement=v_a.winner in {"A", "B"},
                calls=calls,
            )

        v_b, call_b = await self.leaf.compare(
            case_input,
            candidate_output,
            ref,
            position="B_FIRST",
            sub_run_index=sub_run_index,
            mock=mock,
        )
        calls.append(call_b)

        cand_wins_a = v_a.winner == "A"
        ref_wins_a = v_a.winner == "B"
        cand_wins_b = v_b.winner == "B"
        ref_wins_b = v_b.winner == "A"

        if cand_wins_a and cand_wins_b:
            score, agreement = 1.0, True
        elif ref_wins_a and ref_wins_b:
            score, agreement = 0.0, True
        else:
            score, agreement = 0.5, False

        return LeafVerdict(score=score, agreement=agreement, calls=calls)


def _winner_to_score(winner: str | None, *, candidate_is: str) -> float:
    if winner is None or winner == "tie":
        return 0.5
    return 1.0 if winner == candidate_is else 0.0
