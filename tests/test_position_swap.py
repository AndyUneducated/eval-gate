"""PositionSwapJudge: removes position bias from pairwise judging.

Verifies the four canonical outcomes:
- both runs say candidate wins -> score=1.0, agreement=True
- both runs say reference wins -> score=0.0, agreement=True
- disagree -> score=0.5, agreement=False
- either side ties -> score=0.5, agreement=False
"""

from __future__ import annotations

import pytest

from evalgate.judge.pairwise import PairwiseJudge
from evalgate.judge.position_swap import PositionSwapJudge
from evalgate.judge.prompt_spec import JudgeSpec

_SPEC = JudgeSpec(model="ollama/qwen2.5:7b", rubric="x")


def _swap() -> PositionSwapJudge:
    return PositionSwapJudge(PairwiseJudge(_SPEC), enabled=True)


@pytest.mark.asyncio
async def test_candidate_wins_both_orderings():
    swap = _swap()
    # In A_FIRST: candidate is "A"; in B_FIRST: candidate is "B".
    verdict, calls = await swap.score(
        "input",
        "candidate",
        "reference",
        mock_response_a='{"winner": "A", "reason": ""}',
        mock_response_b='{"winner": "B", "reason": ""}',
    )
    assert verdict.score == 1.0
    assert verdict.agreement is True
    assert len(calls) == 2
    assert {c.position for c in calls} == {"A_FIRST", "B_FIRST"}


@pytest.mark.asyncio
async def test_reference_wins_both_orderings():
    swap = _swap()
    verdict, _ = await swap.score(
        "input",
        "candidate",
        "reference",
        mock_response_a='{"winner": "B", "reason": ""}',
        mock_response_b='{"winner": "A", "reason": ""}',
    )
    assert verdict.score == 0.0
    assert verdict.agreement is True


@pytest.mark.asyncio
async def test_disagreement_collapses_to_half():
    swap = _swap()
    # A_FIRST: candidate=A wins; B_FIRST: A wins again means *reference* wins
    # the second pass. Two conflicting verdicts -> 0.5.
    verdict, _ = await swap.score(
        "input",
        "candidate",
        "reference",
        mock_response_a='{"winner": "A", "reason": ""}',
        mock_response_b='{"winner": "A", "reason": ""}',
    )
    assert verdict.score == 0.5
    assert verdict.agreement is False


@pytest.mark.asyncio
async def test_tie_in_first_pass_collapses_to_half():
    swap = _swap()
    verdict, _ = await swap.score(
        "input",
        "candidate",
        "reference",
        mock_response_a='{"winner": "tie", "reason": ""}',
        mock_response_b='{"winner": "B", "reason": ""}',
    )
    assert verdict.score == 0.5
    assert verdict.agreement is False


@pytest.mark.asyncio
async def test_disabled_swap_only_calls_once():
    swap = PositionSwapJudge(PairwiseJudge(_SPEC), enabled=False)
    verdict, calls = await swap.score(
        "input",
        "candidate",
        "reference",
        mock_response_a='{"winner": "A", "reason": ""}',
    )
    assert len(calls) == 1
    assert verdict.score == 1.0  # candidate is A in A_FIRST
