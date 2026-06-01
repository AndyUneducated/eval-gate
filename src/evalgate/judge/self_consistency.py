"""SelfConsistencyJudge: run the leaf judge K times and turn variance into confidence.

A single judge call is noisy — temperature, sampling, prompt phrasing all
push the score around. "Self-consistency" (Wang et al. 2022) re-samples the
same judge K times and treats the **standard deviation** of the resulting
scores as an inverse signal of confidence:

    confidence = 1 - normalized_std

where ``normalized_std = stdev / max_possible_stdev``. For scores in
[0, 1], the worst case is half the samples at 0 and half at 1, giving
``stdev = 0.5``; dividing by 0.5 maps confidence onto [0, 1].

K=1 is a degenerate case: the formula gives ``confidence=1.0``, which is
honest — a single sample has no variance signal at all.
"""

from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass
from typing import Any

from evalgate.judge.protocol import MAX_STD_SCORE_SPREAD, JudgeCallRecord, LeafJudge, LeafVerdict


@dataclass
class SelfConsistencyVerdict:
    mean_score: float
    confidence: float
    per_run_scores: list[float]
    agreements: list[bool] | None


class SelfConsistencyJudge:
    def __init__(self, leaf: LeafJudge, *, k: int, concurrency: int = 4):
        if k < 1:
            raise ValueError("k must be >= 1")
        self.leaf = leaf
        self.k = k
        self.semaphore = asyncio.Semaphore(concurrency)

    async def score(
        self,
        case_input: Any,
        candidate_output: str,
        reference_output: str | None = None,
        *,
        mock: bool = False,
    ) -> tuple[SelfConsistencyVerdict, list[JudgeCallRecord]]:
        async def one(idx: int) -> LeafVerdict:
            async with self.semaphore:
                return await self.leaf.score(
                    case_input,
                    candidate_output,
                    reference_output,
                    sub_run_index=idx,
                    mock=mock,
                )

        results = await asyncio.gather(*(one(i) for i in range(self.k)))
        scores = [r.score for r in results]
        agreements_raw = [r.agreement for r in results]
        agreements: list[bool] | None = (
            [bool(a) for a in agreements_raw]
            if any(a is not None for a in agreements_raw)
            else None
        )
        calls: list[JudgeCallRecord] = []
        for r in results:
            calls.extend(r.calls)

        mean = sum(scores) / len(scores)
        std = statistics.pstdev(scores) if len(scores) > 1 else 0.0
        confidence = max(0.0, 1.0 - (std / MAX_STD_SCORE_SPREAD))

        verdict = SelfConsistencyVerdict(
            mean_score=mean,
            confidence=confidence,
            per_run_scores=scores,
            agreements=agreements,
        )
        return verdict, calls
