"""PositionSwapJudge: removes positional bias from pairwise judging.

Pairwise LLM judges are well-known to favour whichever answer they read
first (the "first-position bias", see Zheng et al. 2023). Mitigation is
mechanical: call the underlying ``PairwiseJudge`` twice with swapped A/B
positions and only trust verdicts that **agree across both orderings**.

Aggregation rules (this layer):
- both orderings prefer candidate -> ``score=1.0, agreement=True``
- both prefer reference                -> ``score=0.0, agreement=True``
- disagree / either is a tie / parse failure -> ``score=0.5, agreement=False``

We always emit two leaf ``JudgeCallRecord`` rows (one per ordering) plus a
third aggregated record carrying the 0/0.5/1 ``score`` so downstream
analytics can read either granularity directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evalgate.judge.pairwise import PairwiseJudge
from evalgate.judge.protocol import JudgeCallRecord


@dataclass
class SwapVerdict:
    score: float  # 1.0 / 0.5 / 0.0
    agreement: bool
    winner_a_first: str | None
    winner_b_first: str | None
    raw: dict[str, Any]


class PositionSwapJudge:
    def __init__(self, leaf: PairwiseJudge, *, enabled: bool = True):
        self.leaf = leaf
        self.enabled = enabled

    async def score(
        self,
        case_input: Any,
        candidate_output: str,
        reference_output: str,
        *,
        sub_run_index: int = 0,
        mock_response_a: str | None = None,
        mock_response_b: str | None = None,
    ) -> tuple[SwapVerdict, list[JudgeCallRecord]]:
        v_a, call_a = await self.leaf.compare(
            case_input,
            candidate_output,
            reference_output,
            position="A_FIRST",
            sub_run_index=sub_run_index,
            mock_response=mock_response_a,
        )

        calls: list[JudgeCallRecord] = [call_a]

        if not self.enabled:
            score = _winner_to_score(v_a.winner, candidate_is="A")
            verdict = SwapVerdict(
                score=score,
                agreement=v_a.winner in {"A", "B", "tie"},
                winner_a_first=v_a.winner,
                winner_b_first=None,
                raw={"a_first": v_a.raw},
            )
            return verdict, calls

        v_b, call_b = await self.leaf.compare(
            case_input,
            candidate_output,
            reference_output,
            position="B_FIRST",
            sub_run_index=sub_run_index,
            mock_response=mock_response_b,
        )
        calls.append(call_b)

        # In A_FIRST: candidate is "A". In B_FIRST: candidate is "B".
        # "Agreement" = both runs picked the same physical answer.
        cand_wins_a = v_a.winner == "A"
        ref_wins_a = v_a.winner == "B"
        cand_wins_b = v_b.winner == "B"
        ref_wins_b = v_b.winner == "A"

        if cand_wins_a and cand_wins_b:
            score, agreement = 1.0, True
        elif ref_wins_a and ref_wins_b:
            score, agreement = 0.0, True
        else:
            # ties, parse fails, or disagreements all collapse to 0.5
            score, agreement = 0.5, False

        verdict = SwapVerdict(
            score=score,
            agreement=agreement,
            winner_a_first=v_a.winner,
            winner_b_first=v_b.winner,
            raw={"a_first": v_a.raw, "b_first": v_b.raw},
        )
        return verdict, calls


def _winner_to_score(winner: str | None, *, candidate_is: str) -> float:
    if winner is None or winner == "tie":
        return 0.5
    return 1.0 if winner == candidate_is else 0.0
