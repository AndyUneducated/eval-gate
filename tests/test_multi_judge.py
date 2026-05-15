"""MultiJudge: aggregate N sub-judges into score/confidence/votes.

Cross-judge variance also drives confidence down — even when each sub-judge
is internally consistent, large disagreement *across* judges reduces trust.
"""

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


@pytest.mark.asyncio
async def test_two_agreeing_judges_high_confidence():
    multi = _stack(["ollama/judge-a", "ollama/judge-b"], k=2)
    agg = await multi.score(
        "input",
        "output",
        mock_scores_per_judge=[[0.8, 0.8], [0.8, 0.8]],
    )
    assert agg.score == pytest.approx(0.8)
    assert agg.confidence == pytest.approx(1.0)
    assert set(agg.votes.keys()) == {"ollama/judge-a", "ollama/judge-b"}
    assert agg.votes["ollama/judge-a"] == pytest.approx(0.8)
    # 2 judges x K=2 runs each = 4 raw calls
    assert len(agg.raw_calls) == 4


@pytest.mark.asyncio
async def test_disagreeing_judges_low_confidence():
    # Each sub-judge is internally consistent (per-judge std = 0) but they
    # disagree wildly across judges (one says 0.0, the other 1.0).
    multi = _stack(["ollama/judge-a", "ollama/judge-b"], k=2)
    agg = await multi.score(
        "input",
        "output",
        mock_scores_per_judge=[[0.0, 0.0], [1.0, 1.0]],
    )
    assert agg.score == pytest.approx(0.5)
    # cross_std = 0.5 = max -> cross_term = 0 -> overall conf = 0
    assert agg.confidence == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_single_judge_falls_back_to_self_consistency_only():
    multi = _stack(["ollama/only-judge"], k=3)
    agg = await multi.score(
        "input",
        "output",
        mock_scores_per_judge=[[0.7, 0.7, 0.7]],
    )
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
