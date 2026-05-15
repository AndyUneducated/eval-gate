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
honest — a single sample has no variance signal at all, so we pass through
the raw score and let downstream gates decide what to do with the
no-information case.

Temperature: when K>1 and the caller didn't bump it, force
``temperature >= 0.7`` for the leaf calls. Otherwise greedy decoding makes
all K samples identical and the variance signal collapses to zero.
"""

from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass
from typing import Any, Protocol

from evalgate.judge.pointwise import PointwiseJudge
from evalgate.judge.position_swap import PositionSwapJudge
from evalgate.judge.protocol import JudgeCallRecord

_MAX_STD = 0.5  # max stdev of scores constrained to [0, 1]
_MIN_TEMP_FOR_VARIANCE = 0.7


@dataclass
class SelfConsistencyVerdict:
    mean_score: float
    confidence: float
    per_run_scores: list[float]
    agreements: list[bool] | None  # only set when leaf is PositionSwap


class _LeafJudge(Protocol):
    """Either PointwiseJudge or PositionSwapJudge — both expose ``score(...)``
    that returns ``(verdict-with-.score, list[JudgeCallRecord])``.

    PointwiseJudge currently returns ``(PointwiseVerdict, JudgeCallRecord)``,
    so we wrap it in :func:`_call_leaf` to normalise the shape.
    """


def _bump_temperature_for_variance(judge: Any) -> None:
    """Best-effort: ensure the leaf's params allow K runs to differ.

    Only acts when the spec doesn't already set a non-zero temperature.
    Hard-coded threshold; keep it boring.
    """
    spec = getattr(judge, "spec", None)
    if spec is None:
        # PositionSwapJudge -> recurse into its leaf
        leaf = getattr(judge, "leaf", None)
        if leaf is not None:
            _bump_temperature_for_variance(leaf)
        return
    params = dict(spec.params or {})
    current = params.get("temperature")
    if current is None or current < _MIN_TEMP_FOR_VARIANCE:
        params["temperature"] = _MIN_TEMP_FOR_VARIANCE
        spec.params = params


async def _call_leaf(
    leaf: _LeafJudge,
    case_input: Any,
    candidate_output: str,
    reference_output: str | None,
    *,
    sub_run_index: int,
    mock_score: float | None,
) -> tuple[float, bool | None, list[JudgeCallRecord]]:
    """Normalise leaf return shape to ``(score, agreement_or_none, calls)``."""
    if isinstance(leaf, PositionSwapJudge):
        mock = _score_to_mock_pair(mock_score)
        verdict, calls = await leaf.score(
            case_input,
            candidate_output,
            reference_output or "",
            sub_run_index=sub_run_index,
            mock_response_a=mock[0],
            mock_response_b=mock[1],
        )
        return verdict.score, verdict.agreement, calls

    if isinstance(leaf, PointwiseJudge):
        verdict, call = await leaf.score(
            case_input,
            candidate_output,
            sub_run_index=sub_run_index,
            mock_response=_score_to_mock_pointwise(mock_score),
        )
        return verdict.score, None, [call]

    raise TypeError(f"Unsupported leaf judge: {type(leaf).__name__}")


def _score_to_mock_pointwise(score: float | None) -> str | None:
    if score is None:
        return None
    return f'{{"score": {score}, "reason": "mock"}}'


def _score_to_mock_pair(score: float | None) -> tuple[str | None, str | None]:
    if score is None:
        return None, None
    if score >= 1.0:
        # candidate is A_FIRST -> A wins; candidate is B_FIRST -> B wins
        return ('{"winner": "A", "reason": "mock"}', '{"winner": "B", "reason": "mock"}')
    if score <= 0.0:
        return ('{"winner": "B", "reason": "mock"}', '{"winner": "A", "reason": "mock"}')
    return ('{"winner": "tie", "reason": "mock"}', '{"winner": "tie", "reason": "mock"}')


class SelfConsistencyJudge:
    def __init__(self, leaf: _LeafJudge, *, k: int, concurrency: int = 4):
        if k < 1:
            raise ValueError("k must be >= 1")
        self.leaf = leaf
        self.k = k
        self.semaphore = asyncio.Semaphore(concurrency)
        if k > 1:
            _bump_temperature_for_variance(leaf)

    async def score(
        self,
        case_input: Any,
        candidate_output: str,
        reference_output: str | None = None,
        *,
        mock_scores: list[float] | None = None,
    ) -> tuple[SelfConsistencyVerdict, list[JudgeCallRecord]]:
        async def one(idx: int) -> tuple[float, bool | None, list[JudgeCallRecord]]:
            async with self.semaphore:
                mock = None
                if mock_scores is not None and idx < len(mock_scores):
                    mock = mock_scores[idx]
                return await _call_leaf(
                    self.leaf,
                    case_input,
                    candidate_output,
                    reference_output,
                    sub_run_index=idx,
                    mock_score=mock,
                )

        results = await asyncio.gather(*(one(i) for i in range(self.k)))
        scores = [r[0] for r in results]
        agreements_raw = [r[1] for r in results]
        agreements: list[bool] | None = (
            [bool(a) for a in agreements_raw]
            if any(a is not None for a in agreements_raw)
            else None
        )
        calls: list[JudgeCallRecord] = []
        for r in results:
            calls.extend(r[2])

        mean = sum(scores) / len(scores)
        std = statistics.pstdev(scores) if len(scores) > 1 else 0.0
        confidence = max(0.0, 1.0 - (std / _MAX_STD))

        verdict = SelfConsistencyVerdict(
            mean_score=mean,
            confidence=confidence,
            per_run_scores=scores,
            agreements=agreements,
        )
        return verdict, calls
