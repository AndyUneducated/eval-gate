"""PairwiseJudge — A/B winner extraction."""

from __future__ import annotations

import pytest

from evalgate.judge.pairwise import PairwiseJudge
from evalgate.judge.prompt_spec import JudgeSpec

_SPEC = JudgeSpec(model="ollama/qwen3.5:9b", rubric="(ignored in pairwise mode)")


def _patch(monkeypatch, response: str) -> None:
    async def fake(**kwargs):
        return response, {}

    monkeypatch.setattr("evalgate.judge.pairwise.acompletion_json", fake)


@pytest.mark.asyncio
async def test_parses_winner_a(monkeypatch):
    _patch(monkeypatch, '{"winner": "A", "reason": "more accurate"}')
    verdict, call = await PairwiseJudge(_SPEC).compare(
        "input", "candidate text", "reference text", position="A_FIRST", mock=False
    )
    assert verdict.winner == "A"
    assert call.position == "A_FIRST"
    assert call.winner == "A"
    assert call.score is None


@pytest.mark.asyncio
async def test_parses_winner_b_position_swapped(monkeypatch):
    _patch(monkeypatch, '{"winner": "B", "reason": "..."}')
    verdict, call = await PairwiseJudge(_SPEC).compare(
        "input", "candidate text", "reference text", position="B_FIRST", mock=False
    )
    assert verdict.winner == "B"
    assert call.position == "B_FIRST"


@pytest.mark.asyncio
async def test_regex_fallback_on_prose(monkeypatch):
    _patch(monkeypatch, "After careful review, the winner is A overall.")
    verdict, _ = await PairwiseJudge(_SPEC).compare(
        "input", "candidate", "reference", mock=False
    )
    assert verdict.winner == "A"


@pytest.mark.asyncio
async def test_tie_recognized(monkeypatch):
    _patch(monkeypatch, '{"winner": "tie", "reason": "comparable"}')
    verdict, _ = await PairwiseJudge(_SPEC).compare(
        "input", "candidate", "reference", mock=False
    )
    assert verdict.winner == "tie"


@pytest.mark.asyncio
async def test_unparseable_returns_none_winner(monkeypatch):
    _patch(monkeypatch, "completely off-topic response")
    verdict, _ = await PairwiseJudge(_SPEC).compare(
        "input", "candidate", "reference", mock=False
    )
    assert verdict.winner is None
