"""MultiJudge: aggregate N sub-judges into score/confidence/votes."""

from __future__ import annotations

import pytest

from evalgate.judge.multi_judge import MultiJudge, build_judge_stack
from evalgate.judge.pointwise import PointwiseJudge
from evalgate.judge.prompt_spec import (
    CandidateSpec,
    JudgePolicySpec,
    JudgeSpec,
    PromptSpec,
)
from evalgate.judge.self_consistency import SelfConsistencyJudge


def _stack(judge_models: list[str], *, k: int = 1, concurrency: int = 2) -> MultiJudge:
    subs = [
        SelfConsistencyJudge(
            PointwiseJudge(JudgeSpec(model=m, rubric="rate")),
            k=k,
            concurrency=concurrency,
        )
        for m in judge_models
    ]
    policy = JudgePolicySpec(mode="pointwise", k=k, concurrency=concurrency)
    return MultiJudge(subs, policy)


def _patch_pointwise_by_model(monkeypatch, model_scores: dict[str, list[float]]) -> None:
    """Route acompletion_json by ``model`` kwarg to per-judge score sequences."""
    queues: dict[str, list[str]] = {
        model: [f'{{"score": {s}, "reason": ""}}' for s in scores]
        for model, scores in model_scores.items()
    }

    async def fake(**kwargs):
        model = kwargs["model"]
        q = queues.get(model, [])
        text = q.pop(0) if q else '{"score": 0.5, "reason": ""}'
        return text, {}

    monkeypatch.setattr("evalgate.judge.pointwise.acompletion_json", fake)


@pytest.mark.asyncio
async def test_two_agreeing_judges_high_confidence(monkeypatch):
    _patch_pointwise_by_model(
        monkeypatch,
        {
            "ollama/judge-a": [0.8, 0.8],
            "ollama/judge-b": [0.8, 0.8],
        },
    )
    multi = _stack(["ollama/judge-a", "ollama/judge-b"], k=2)
    agg = await multi.score("input", "output", mock=False)
    assert agg.score == pytest.approx(0.8)
    assert agg.confidence == pytest.approx(1.0)
    assert set(agg.votes.keys()) == {"ollama/judge-a", "ollama/judge-b"}
    assert agg.votes["ollama/judge-a"] == pytest.approx(0.8)
    assert len(agg.raw_calls) == 4


@pytest.mark.asyncio
async def test_disagreeing_judges_low_confidence(monkeypatch):
    _patch_pointwise_by_model(
        monkeypatch,
        {
            "ollama/judge-a": [0.0, 0.0],
            "ollama/judge-b": [1.0, 1.0],
        },
    )
    multi = _stack(["ollama/judge-a", "ollama/judge-b"], k=2)
    agg = await multi.score("input", "output", mock=False)
    assert agg.score == pytest.approx(0.5)
    assert agg.confidence == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_all_judges_failing_yields_no_signal(monkeypatch):
    # Every leaf call returns an empty completion (transport failure), so every
    # sub-judge produces score=None. The aggregate must flag ``no_signal`` (a
    # placeholder 0.0) instead of silently reporting a real 0.0 verdict.
    async def fail(**kwargs):
        return "", {}

    monkeypatch.setattr("evalgate.judge.pointwise.acompletion_json", fail)
    multi = _stack(["ollama/judge-a", "ollama/judge-b"], k=2)
    agg = await multi.score("input", "output", mock=False)
    assert agg.no_signal is True
    assert agg.votes == {}
    assert agg.confidence == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_single_judge_falls_back_to_self_consistency_only(monkeypatch):
    _patch_pointwise_by_model(monkeypatch, {"ollama/only-judge": [0.7, 0.7, 0.7]})
    multi = _stack(["ollama/only-judge"], k=3)
    agg = await multi.score("input", "output", mock=False)
    assert agg.score == pytest.approx(0.7)
    assert agg.confidence == pytest.approx(1.0)
    assert list(agg.votes.keys()) == ["ollama/only-judge"]


def test_build_judge_stack_pointwise():
    spec = PromptSpec(
        name="x",
        candidate=CandidateSpec(model="m"),
        judges=[JudgeSpec(model="j1", rubric="r"), JudgeSpec(model="j2", rubric="r")],
        judge_policy=JudgePolicySpec(mode="pointwise", k=2),
    )
    stack = build_judge_stack(spec)
    assert len(stack.sub_judges) == 2
    assert isinstance(stack.sub_judges[0].leaf, PointwiseJudge)


def test_build_judge_stack_pairwise_wraps_position_swap():
    from evalgate.judge.position_swap import PositionSwapJudge

    spec = PromptSpec(
        name="x",
        candidate=CandidateSpec(model="m"),
        judges=[JudgeSpec(model="j1", rubric="r")],
        judge_policy=JudgePolicySpec(mode="pairwise", k=1, position_swap=True),
    )
    stack = build_judge_stack(spec)
    assert isinstance(stack.sub_judges[0].leaf, PositionSwapJudge)
