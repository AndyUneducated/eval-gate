"""PositionSwapJudge: removes position bias from pairwise judging."""

from __future__ import annotations

import pytest

from evalgate.judge.pairwise import PairwiseJudge
from evalgate.judge.position_swap import PositionSwapJudge
from evalgate.judge.prompt_spec import JudgeSpec

_SPEC = JudgeSpec(model="ollama/qwen3.5:9b", rubric="x")


def _patch_pairwise(monkeypatch, responses: list[str]) -> None:
    queue = list(responses)

    async def fake(**kwargs):
        text = queue.pop(0) if queue else "{}"
        return text, {}

    monkeypatch.setattr("evalgate.judge.pairwise.acompletion_json", fake)


def _swap() -> PositionSwapJudge:
    return PositionSwapJudge(PairwiseJudge(_SPEC), enabled=True)


@pytest.mark.asyncio
async def test_candidate_wins_both_orderings(monkeypatch):
    _patch_pairwise(
        monkeypatch,
        ['{"winner": "A", "reason": ""}', '{"winner": "B", "reason": ""}'],
    )
    verdict = await _swap().score("input", "candidate", "reference", mock=False)
    assert verdict.score == 1.0
    assert verdict.agreement is True
    assert len(verdict.calls) == 2
    assert {c.position for c in verdict.calls} == {"A_FIRST", "B_FIRST"}


@pytest.mark.asyncio
async def test_reference_wins_both_orderings(monkeypatch):
    _patch_pairwise(
        monkeypatch,
        ['{"winner": "B", "reason": ""}', '{"winner": "A", "reason": ""}'],
    )
    verdict = await _swap().score("input", "candidate", "reference", mock=False)
    assert verdict.score == 0.0
    assert verdict.agreement is True


@pytest.mark.asyncio
async def test_disagreement_collapses_to_half(monkeypatch):
    _patch_pairwise(
        monkeypatch,
        ['{"winner": "A", "reason": ""}', '{"winner": "A", "reason": ""}'],
    )
    verdict = await _swap().score("input", "candidate", "reference", mock=False)
    assert verdict.score == 0.5
    assert verdict.agreement is False


@pytest.mark.asyncio
async def test_tie_in_first_pass_collapses_to_half(monkeypatch):
    _patch_pairwise(
        monkeypatch,
        ['{"winner": "tie", "reason": ""}', '{"winner": "B", "reason": ""}'],
    )
    verdict = await _swap().score("input", "candidate", "reference", mock=False)
    assert verdict.score == 0.5
    assert verdict.agreement is False


@pytest.mark.asyncio
async def test_disabled_swap_only_calls_once(monkeypatch):
    _patch_pairwise(monkeypatch, ['{"winner": "A", "reason": ""}'])
    swap = PositionSwapJudge(PairwiseJudge(_SPEC), enabled=False)
    verdict = await swap.score("input", "candidate", "reference", mock=False)
    assert len(verdict.calls) == 1
    assert verdict.score == 1.0
