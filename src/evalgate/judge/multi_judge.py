"""MultiJudge: aggregate N sub-judges into a single score + confidence."""

from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass
from typing import Any

from evalgate.judge.pairwise import PairwiseJudge
from evalgate.judge.pointwise import PointwiseJudge
from evalgate.judge.position_swap import PositionSwapJudge
from evalgate.judge.prompt_spec import JudgePolicySpec, JudgeSpec, PromptSpec
from evalgate.judge.protocol import MAX_STD_SCORE_SPREAD, JudgeCallRecord, LeafJudge
from evalgate.judge.self_consistency import SelfConsistencyJudge

_MIN_TEMP_FOR_VARIANCE = 0.7


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
        mock: bool = False,
    ) -> JudgeAggregate:
        async def one(sub: SelfConsistencyJudge):
            async with self.semaphore:
                return await sub.score(
                    case_input,
                    candidate_output,
                    reference_output,
                    mock=mock,
                )

        results = await asyncio.gather(*(one(sub) for sub in self.sub_judges))

        votes: dict[str, float] = {}
        per_judge_conf: dict[str, float] = {}
        all_calls: list[JudgeCallRecord] = []
        per_judge_means: list[float] = []

        for sub, (verdict, calls) in zip(self.sub_judges, results, strict=True):
            model = sub.leaf.model
            votes[model] = verdict.mean_score
            per_judge_conf[model] = verdict.confidence
            per_judge_means.append(verdict.mean_score)
            all_calls.extend(calls)

        score = sum(per_judge_means) / len(per_judge_means)
        cross_std = statistics.pstdev(per_judge_means) if len(per_judge_means) > 1 else 0.0
        cross_term = max(0.0, 1.0 - (cross_std / MAX_STD_SCORE_SPREAD))
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


def _ensure_variance_temperature(judge: JudgeSpec, k: int) -> JudgeSpec:
    """When K>1, bump temperature so self-consistency samples can differ."""
    if k <= 1:
        return judge
    params = dict(judge.params or {})
    current = params.get("temperature")
    if current is None or current < _MIN_TEMP_FOR_VARIANCE:
        params["temperature"] = _MIN_TEMP_FOR_VARIANCE
    return judge.model_copy(update={"params": params})


def build_judge_stack(spec: PromptSpec) -> MultiJudge:
    """Translate a `PromptSpec` into a fully-built MultiJudge."""
    policy = spec.judge_policy
    sub_judges: list[SelfConsistencyJudge] = []
    for j in spec.judges:
        j_spec = _ensure_variance_temperature(j, policy.k)
        if policy.mode == "pointwise":
            leaf: LeafJudge = PointwiseJudge(j_spec)
        else:
            leaf = PositionSwapJudge(PairwiseJudge(j_spec), enabled=policy.position_swap)
        sub_judges.append(SelfConsistencyJudge(leaf, k=policy.k, concurrency=policy.concurrency))
    return MultiJudge(sub_judges, policy)
