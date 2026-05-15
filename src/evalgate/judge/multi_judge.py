"""MultiJudge: aggregate N sub-judges into a single score + confidence.

This is the outermost layer of the Phase 6 stack. For each prompt we build
one ``SelfConsistencyJudge`` per ``judges[]`` entry (each itself wrapping a
``PointwiseJudge`` or ``PositionSwapJudge``), run them concurrently, and
combine their per-sub-judge ``mean_score`` into a single final score.

Aggregation:
- final score = mean of per-judge mean_scores
- final confidence = product(per_judge_confidence) * (1 - normalised cross-judge spread)
  - product term: any single noisy judge drags confidence down
  - spread term: even if every judge is internally consistent, large
    disagreement *across* judges is a louder distrust signal
- votes: { "<judge_model>": mean_score } for audit / Phase 7 BadCase

Used by ``judge.runner.iter_eval``; not used directly by anything else.
"""

from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass
from typing import Any

from evalgate.judge.pairwise import PairwiseJudge
from evalgate.judge.pointwise import PointwiseJudge
from evalgate.judge.position_swap import PositionSwapJudge
from evalgate.judge.prompt_spec import JudgePolicySpec, PromptSpec
from evalgate.judge.protocol import JudgeCallRecord
from evalgate.judge.self_consistency import SelfConsistencyJudge

_MAX_STD = 0.5


@dataclass
class JudgeAggregate:
    score: float
    confidence: float
    votes: dict[str, float]
    raw_calls: list[JudgeCallRecord]
    per_judge_confidence: dict[str, float]


class MultiJudge:
    def __init__(self, sub_judges: list[SelfConsistencyJudge], policy: JudgePolicySpec):
        if not sub_judges:
            raise ValueError("MultiJudge needs at least one sub-judge")
        self.sub_judges = sub_judges
        self.policy = policy
        self.semaphore = asyncio.Semaphore(policy.concurrency)

    async def score(
        self,
        case_input: Any,
        candidate_output: str,
        reference_output: str | None = None,
        *,
        mock_scores_per_judge: list[list[float]] | None = None,
    ) -> JudgeAggregate:
        async def one(idx: int, sub: SelfConsistencyJudge):
            async with self.semaphore:
                mock_scores = (
                    mock_scores_per_judge[idx]
                    if mock_scores_per_judge and idx < len(mock_scores_per_judge)
                    else None
                )
                return await sub.score(
                    case_input,
                    candidate_output,
                    reference_output,
                    mock_scores=mock_scores,
                )

        results = await asyncio.gather(*(one(i, sub) for i, sub in enumerate(self.sub_judges)))

        votes: dict[str, float] = {}
        per_judge_conf: dict[str, float] = {}
        all_calls: list[JudgeCallRecord] = []
        per_judge_means: list[float] = []

        for sub, (verdict, calls) in zip(self.sub_judges, results, strict=True):
            model = _judge_model_for(sub)
            votes[model] = verdict.mean_score
            per_judge_conf[model] = verdict.confidence
            per_judge_means.append(verdict.mean_score)
            all_calls.extend(calls)

        score = sum(per_judge_means) / len(per_judge_means)
        cross_std = statistics.pstdev(per_judge_means) if len(per_judge_means) > 1 else 0.0
        cross_term = max(0.0, 1.0 - (cross_std / _MAX_STD))
        prod_term = 1.0
        for c in per_judge_conf.values():
            prod_term *= c
        confidence = max(0.0, min(1.0, prod_term * cross_term))

        return JudgeAggregate(
            score=score,
            confidence=confidence,
            votes=votes,
            raw_calls=all_calls,
            per_judge_confidence=per_judge_conf,
        )


def _judge_model_for(sub: SelfConsistencyJudge) -> str:
    leaf = sub.leaf
    if isinstance(leaf, PositionSwapJudge):
        return leaf.leaf.spec.model
    if isinstance(leaf, PointwiseJudge):
        return leaf.spec.model
    return type(leaf).__name__


def build_judge_stack(spec: PromptSpec) -> MultiJudge:
    """Translate a `PromptSpec` into a fully-built MultiJudge.

    Mode handling:
    - ``pointwise``: each sub-judge = ``SelfConsistency(PointwiseJudge(...))``
    - ``pairwise``:  each sub-judge = ``SelfConsistency(PositionSwap(PairwiseJudge(...)))``
      Requires `case.expected` at runner level (enforced there, not here, so
      this builder stays pure and side-effect-free).
    """
    policy = spec.judge_policy
    sub_judges: list[SelfConsistencyJudge] = []
    for j in spec.judges:
        if policy.mode == "pointwise":
            leaf: Any = PointwiseJudge(j)
        else:
            leaf = PositionSwapJudge(PairwiseJudge(j), enabled=policy.position_swap)
        sub_judges.append(SelfConsistencyJudge(leaf, k=policy.k, concurrency=policy.concurrency))
    return MultiJudge(sub_judges, policy)
