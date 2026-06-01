"""SelfConsistencyJudge: K runs + variance -> confidence."""

from __future__ import annotations

import pytest

from evalgate.judge.pointwise import PointwiseJudge
from evalgate.judge.prompt_spec import JudgeSpec
from evalgate.judge.self_consistency import SelfConsistencyJudge

_SPEC = JudgeSpec(model="ollama/qwen3.5:9b", rubric="rate")


def _patch_pointwise(monkeypatch, scores: list[float]) -> None:
    queue = [f'{{"score": {s}, "reason": ""}}' for s in scores]

    async def fake(**kwargs):
        text = queue.pop(0) if queue else '{"score": 0.5, "reason": ""}'
        return text, {}

    monkeypatch.setattr("evalgate.judge.pointwise.acompletion_json", fake)


@pytest.mark.asyncio
async def test_identical_scores_full_confidence(monkeypatch):
    _patch_pointwise(monkeypatch, [0.6, 0.6, 0.6])
    sc = SelfConsistencyJudge(PointwiseJudge(_SPEC), k=3, concurrency=2)
    verdict, calls = await sc.score("input", "output", mock=False)
    assert verdict.mean_score == pytest.approx(0.6)
    assert verdict.confidence == pytest.approx(1.0)
    assert len(calls) == 3
    assert {c.sub_run_index for c in calls} == {0, 1, 2}


@pytest.mark.asyncio
async def test_high_variance_low_confidence(monkeypatch):
    _patch_pointwise(monkeypatch, [0.0, 1.0, 0.0, 1.0])
    sc = SelfConsistencyJudge(PointwiseJudge(_SPEC), k=4, concurrency=2)
    verdict, _ = await sc.score("input", "output", mock=False)
    assert verdict.mean_score == pytest.approx(0.5)
    assert verdict.confidence == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_partial_variance_partial_confidence(monkeypatch):
    _patch_pointwise(monkeypatch, [0.5, 0.6, 0.7])
    sc = SelfConsistencyJudge(PointwiseJudge(_SPEC), k=3, concurrency=2)
    verdict, _ = await sc.score("input", "output", mock=False)
    assert 0.5 < verdict.confidence < 1.0


@pytest.mark.asyncio
async def test_k_one_is_degenerate_but_legal(monkeypatch):
    _patch_pointwise(monkeypatch, [0.42])
    sc = SelfConsistencyJudge(PointwiseJudge(_SPEC), k=1, concurrency=2)
    verdict, calls = await sc.score("input", "output", mock=False)
    assert verdict.mean_score == pytest.approx(0.42)
    assert verdict.confidence == pytest.approx(1.0)
    assert len(calls) == 1
