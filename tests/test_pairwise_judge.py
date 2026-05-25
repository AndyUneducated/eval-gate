"""PairwiseJudge — A/B winner extraction.

PairwiseJudge does NOT compute a 0..1 score (that's PositionSwapJudge's job).
It parses ``{"winner": "A"|"B"|"tie", "reason": "..."}`` (case-insensitive,
regex fallback on prose).
"""

from __future__ import annotations

import pytest

from evalgate.judge.pairwise import PairwiseJudge
from evalgate.judge.prompt_spec import JudgeSpec

_SPEC = JudgeSpec(model="ollama/qwen3.5:9b", rubric="(ignored in pairwise mode)")


@pytest.mark.asyncio
async def test_parses_winner_a():
    judge = PairwiseJudge(_SPEC)
    verdict, call = await judge.compare(
        "input",
        "candidate text",
        "reference text",
        position="A_FIRST",
        mock_response='{"winner": "A", "reason": "more accurate"}',
    )
    assert verdict.winner == "A"
    assert call.position == "A_FIRST"
    assert call.winner == "A"
    assert call.score is None


@pytest.mark.asyncio
async def test_parses_winner_b_position_swapped():
    judge = PairwiseJudge(_SPEC)
    verdict, call = await judge.compare(
        "input",
        "candidate text",
        "reference text",
        position="B_FIRST",
        mock_response='{"winner": "B", "reason": "..."}',
    )
    assert verdict.winner == "B"
    assert call.position == "B_FIRST"


@pytest.mark.asyncio
async def test_regex_fallback_on_prose():
    judge = PairwiseJudge(_SPEC)
    verdict, _ = await judge.compare(
        "input",
        "candidate",
        "reference",
        mock_response="After careful review, the winner is A overall.",
    )
    assert verdict.winner == "A"


@pytest.mark.asyncio
async def test_tie_recognized():
    judge = PairwiseJudge(_SPEC)
    verdict, _ = await judge.compare(
        "input",
        "candidate",
        "reference",
        mock_response='{"winner": "tie", "reason": "comparable"}',
    )
    assert verdict.winner == "tie"


@pytest.mark.asyncio
async def test_unparseable_returns_none_winner():
    judge = PairwiseJudge(_SPEC)
    verdict, _ = await judge.compare(
        "input",
        "candidate",
        "reference",
        mock_response="completely off-topic response",
    )
    assert verdict.winner is None
