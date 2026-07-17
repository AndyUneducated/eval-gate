"""MultiJudge: aggregate N sub-judges into a single score + confidence."""

from __future__ import annotations

import asyncio
import math
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

    async def score(
        self,
        case_input: Any,
        candidate_output: str,
        reference_output: str | None = None,
        *,
        mock: bool = False,
    ) -> JudgeAggregate:
        # No outer semaphore here: the sub-judges share one semaphore (wired in
        # ``build_judge_stack``) that bounds *total* leaf calls, so gathering all
        # sub-judges freely keeps in-flight load at ``concurrency`` (not squared).
        async def one(sub: SelfConsistencyJudge):
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
        per_judge_confs: list[float] = []

        for idx, (sub, (verdict, calls)) in enumerate(zip(self.sub_judges, results, strict=True)):
            all_calls.extend(calls)
            if verdict.mean_score is None:
                # This sub-judge produced no signal at all (every run failed);
                # drop it rather than fabricate a score.
                continue
            model = sub.leaf.model
            # Same-model ensembles (e.g. 3x one model at different temps) must
            # not collide into a single dict entry — that would silently shrink
            # the judge count the confidence term is computed over.
            key = model if model not in votes else f"{model}#{idx}"
            votes[key] = verdict.mean_score
            per_judge_conf[key] = verdict.confidence
            per_judge_means.append(verdict.mean_score)
            per_judge_confs.append(verdict.confidence)

        if not per_judge_means:
            # Every sub-judge failed — surface a no-confidence neutral result.
            return JudgeAggregate(
                score=0.0,
                confidence=0.0,
                votes=votes,
                raw_calls=all_calls,
                per_judge_confidence=per_judge_conf,
            )

        score = sum(per_judge_means) / len(per_judge_means)
        cross_std = statistics.pstdev(per_judge_means) if len(per_judge_means) > 1 else 0.0
        cross_term = max(0.0, 1.0 - (cross_std / MAX_STD_SCORE_SPREAD))
        # Size-invariant aggregation of per-judge confidence (geometric mean):
        # adding more *agreeing* judges must not drive confidence toward 0, as a
        # naive product would. Cross-judge disagreement is captured separately
        # by ``cross_term``.
        geo_mean = math.exp(
            sum(math.log(max(c, 1e-9)) for c in per_judge_confs) / len(per_judge_confs)
        )
        confidence = max(0.0, min(1.0, geo_mean * cross_term))

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
    # Coerce defensively: a YAML-quoted ``temperature: "0.7"`` arrives as a str,
    # and a raw ``<`` comparison against it would raise TypeError and abort the
    # whole run (this runs outside the per-case try/except).
    try:
        current_val = float(current) if current is not None else None
    except (TypeError, ValueError):
        current_val = None
    if current_val is None or current_val < _MIN_TEMP_FOR_VARIANCE:
        params["temperature"] = _MIN_TEMP_FOR_VARIANCE
    return judge.model_copy(update={"params": params})


def build_judge_stack(spec: PromptSpec) -> MultiJudge:
    """Translate a `PromptSpec` into a fully-built MultiJudge."""
    policy = spec.judge_policy
    # One semaphore shared across every sub-judge bounds *total* concurrent LLM
    # calls at ``policy.concurrency`` (prevents the concurrency² fan-out that
    # independent per-judge semaphores would permit).
    shared_semaphore = asyncio.Semaphore(policy.concurrency)
    sub_judges: list[SelfConsistencyJudge] = []
    for j in spec.judges:
        j_spec = _ensure_variance_temperature(j, policy.k)
        if policy.mode == "pointwise":
            leaf: LeafJudge = PointwiseJudge(j_spec)
        else:
            leaf = PositionSwapJudge(PairwiseJudge(j_spec), enabled=policy.position_swap)
        sub_judges.append(SelfConsistencyJudge(leaf, k=policy.k, semaphore=shared_semaphore))
    return MultiJudge(sub_judges, policy)
