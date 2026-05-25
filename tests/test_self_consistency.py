"""SelfConsistencyJudge: K runs + variance -> confidence.

Three behaviours we lock in:
- identical scores across K runs -> confidence == 1.0
- maximally split scores (0 / 1 / 0 / 1...) -> confidence == 0.0
- K=1 -> degenerate but legal; confidence = 1.0 (no signal)
"""

from __future__ import annotations

import pytest

from evalgate.judge.pointwise import PointwiseJudge
from evalgate.judge.prompt_spec import JudgeSpec
from evalgate.judge.self_consistency import SelfConsistencyJudge

_SPEC = JudgeSpec(model="ollama/qwen3.5:9b", rubric="rate")


@pytest.mark.asyncio
async def test_identical_scores_full_confidence():
    leaf = PointwiseJudge(_SPEC)
    sc = SelfConsistencyJudge(leaf, k=3, concurrency=2)
    verdict, calls = await sc.score("input", "output", mock_scores=[0.6, 0.6, 0.6])
    assert verdict.mean_score == pytest.approx(0.6)
    assert verdict.confidence == pytest.approx(1.0)
    assert len(calls) == 3
    assert {c.sub_run_index for c in calls} == {0, 1, 2}


@pytest.mark.asyncio
async def test_high_variance_low_confidence():
    leaf = PointwiseJudge(_SPEC)
    sc = SelfConsistencyJudge(leaf, k=4, concurrency=2)
    verdict, _ = await sc.score("input", "output", mock_scores=[0.0, 1.0, 0.0, 1.0])
    assert verdict.mean_score == pytest.approx(0.5)
    # stdev=0.5 = max possible stdev in [0,1] -> confidence collapses to 0.
    assert verdict.confidence == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_partial_variance_partial_confidence():
    leaf = PointwiseJudge(_SPEC)
    sc = SelfConsistencyJudge(leaf, k=3, concurrency=2)
    verdict, _ = await sc.score("input", "output", mock_scores=[0.5, 0.6, 0.7])
    assert 0.5 < verdict.confidence < 1.0


@pytest.mark.asyncio
async def test_k_one_is_degenerate_but_legal():
    leaf = PointwiseJudge(_SPEC)
    sc = SelfConsistencyJudge(leaf, k=1, concurrency=2)
    verdict, calls = await sc.score("input", "output", mock_scores=[0.42])
    assert verdict.mean_score == pytest.approx(0.42)
    assert verdict.confidence == pytest.approx(1.0)  # no variance signal at all
    assert len(calls) == 1
