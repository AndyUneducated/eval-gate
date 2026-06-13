"""RagEvaluator — retriever → generator → ragas metrics, in that order.

Pipeline per case:

1. Resolve ``question`` from ``case.input`` (``input.question`` or
   ``input.prompt`` — both common shapes from Phase 4 case_extract).
2. ``retriever.retrieve(question)`` → list[str] of contexts.
3. Render the candidate's user template with ``{contexts}`` substituted
   in (joined by blank lines), and run the candidate LLM via the
   shared :func:`run_candidate` (so latency / cost accounting matches
   the generic path).
4. Hand ``(question, answer, contexts, reference_contexts,
   reference_answer)`` to :class:`_RagasScorer`, which calls
   ``ragas.evaluate`` once with the metrics requested in
   ``rag_evaluator.metrics``.
5. Pack everything into an :class:`EvaluationOutcome`. ``score`` is the
   simple mean of the per-metric values (so a RAG case still has a
   single quality scalar for bootstrap-CI on the gate's quality axis);
   ``axis_breakdown["quality"]`` carries the per-ragas-metric breakdown.

We deliberately re-do the dataset-of-one each call rather than batching
across cases. The runner is sequential per case (Phase 5 decision) and
ragas's per-metric LLM calls are the dominant cost — batching would only
shave Python overhead.
"""

from __future__ import annotations

import contextlib
import math
from typing import Any

from evalgate.db.models import EvalCaseRow
from evalgate.evaluator.base import EvaluationOutcome
from evalgate.evaluator.rag.retriever import EmbeddingRetriever
from evalgate.judge.candidate import run_candidate
from evalgate.judge.prompt_spec import PromptSpec
from evalgate.judge.protocol import JudgeCallRecord


def _question_for(case: EvalCaseRow) -> str:
    inp = case.input or {}
    for key in ("question", "prompt", "input"):
        val = inp.get(key)
        if isinstance(val, str) and val.strip():
            return val
    # Fall back: stringify the whole input dict so ragas at least gets
    # *something* to work with.
    return str(inp)


def _reference_answer(case: EvalCaseRow) -> str | None:
    expected = case.expected or {}
    for key in ("answer", "output"):
        val = expected.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _format_contexts(contexts: list[str]) -> str:
    return "\n\n".join(c for c in contexts if c)


class RagEvaluator:
    """The router's ``rag`` branch. See module docstring."""

    label = "ragas"

    def __init__(self, spec: PromptSpec, *, mock: bool = False):
        if spec.retriever is None or spec.rag_evaluator is None:
            raise ValueError(
                "RagEvaluator needs both `retriever:` and `rag_evaluator:` set in prompt.yaml"
            )
        self._spec = spec
        self._mock = mock
        self._retriever = EmbeddingRetriever(spec.retriever, mock=mock)
        self._scorer = _RagasScorer(spec, mock=mock)

    async def evaluate(
        self,
        case: EvalCaseRow,
        *,
        mock: bool = False,
    ) -> EvaluationOutcome:
        use_mock = mock or self._mock
        question = _question_for(case)
        ref_answer = _reference_answer(case)
        ref_contexts = list(case.retrieved_contexts or [])

        try:
            contexts = await self._retriever.retrieve(question)
        except Exception as exc:
            return EvaluationOutcome(
                score=0.0,
                output_text="",
                cost_usd=0.0,
                latency_ms=0,
                judge_raw={"error": f"retrieve-failed: {exc}"},
                reason=f"retrieve-failed: {exc}",
                error=True,
                error_kind="retrieve_failure",
            )

        case_input = {
            **(case.input or {}),
            "question": question,
            "contexts": _format_contexts(contexts),
        }
        cand_mock = "mock-rag-answer" if use_mock else None
        try:
            candidate = await run_candidate(case_input, self._spec, mock_response=cand_mock)
        except Exception as exc:
            return EvaluationOutcome(
                score=0.0,
                output_text="",
                cost_usd=0.0,
                latency_ms=0,
                retrieved_contexts=contexts,
                judge_raw={"error": f"candidate-failed: {exc}"},
                reason=f"candidate-failed: {exc}",
                error=True,
                error_kind="candidate_failure",
            )

        try:
            sub_metrics, raw_calls = await self._scorer.score(
                question=question,
                answer=candidate.text,
                contexts=contexts,
                reference_answer=ref_answer,
                reference_contexts=ref_contexts,
            )
        except Exception as exc:
            return EvaluationOutcome(
                score=0.0,
                output_text=candidate.text,
                cost_usd=candidate.cost_usd,
                latency_ms=candidate.latency_ms,
                retrieved_contexts=contexts,
                judge_raw={"error": f"ragas-failed: {exc}"},
                reason=f"ragas-failed: {exc}",
                error=True,
                error_kind="ragas_failure",
            )

        score = _mean_clamped(sub_metrics.values())
        confidence = _confidence_from_sub_metrics(sub_metrics)
        return EvaluationOutcome(
            score=score,
            output_text=candidate.text,
            cost_usd=candidate.cost_usd,
            latency_ms=candidate.latency_ms,
            confidence=confidence,
            axis_breakdown={"quality": dict(sub_metrics)},
            retrieved_contexts=contexts,
            raw_calls=raw_calls,
            judge_raw={
                "metrics": list(sub_metrics.keys()),
                "ragas_llm": self._spec.rag_evaluator.llm_model,
            },
        )


class _RagasScorer:
    """Adapter onto ``ragas.evaluate`` for a single (question, answer,
    contexts, reference) row.

    Constructed lazily inside the evaluator; importing ragas at module
    import time would make pure-generic test runs pull in langchain etc.
    """

    def __init__(self, spec: PromptSpec, *, mock: bool):
        assert spec.rag_evaluator is not None
        self._spec = spec.rag_evaluator
        self._mock = mock
        self._initialised = False
        self._metric_objects: list[Any] = []
        self._llm: Any = None
        self._embeddings: Any = None

    def _initialise(self) -> None:
        if self._initialised:
            return
        from evalgate.evaluator.rag.ragas_adapter import build_ragas_components

        self._llm, self._embeddings = build_ragas_components(self._spec, mock=self._mock)
        self._metric_objects = _build_metrics(self._spec.metrics, self._llm, self._embeddings)
        self._initialised = True

    async def score(
        self,
        *,
        question: str,
        answer: str,
        contexts: list[str],
        reference_answer: str | None,
        reference_contexts: list[str],
    ) -> tuple[dict[str, float], list[JudgeCallRecord]]:
        if self._mock:
            sub_metrics = {name: 0.8 for name in self._spec.metrics}
            raw_calls = [
                JudgeCallRecord(
                    judge_model=f"ragas:{name}",
                    sub_run_index=idx,
                    position=None,
                    score=value,
                    winner=None,
                    reason="mock-ragas-score",
                    raw={"metric": name, "value": value, "mock": True},
                )
                for idx, (name, value) in enumerate(sub_metrics.items())
            ]
            return sub_metrics, raw_calls

        self._initialise()

        from datasets import Dataset
        from ragas import evaluate as ragas_evaluate

        # ragas v0.1 dataset shape: question / answer / contexts / ground_truth.
        # ``reference`` is the v0.2 alias; we pass both for compatibility.
        row = {
            "question": [question],
            "user_input": [question],
            "answer": [answer],
            "response": [answer],
            "contexts": [contexts],
            "retrieved_contexts": [contexts],
            "ground_truth": [reference_answer or ""],
            "reference": [reference_answer or ""],
            "reference_contexts": [reference_contexts],
        }
        dataset = Dataset.from_dict(row)

        # ragas.evaluate is sync; offload to a thread so we don't block
        # the asyncio loop. Internally it spins up its own task group.
        import asyncio

        loop = asyncio.get_running_loop()

        def _run() -> Any:
            return ragas_evaluate(
                dataset=dataset,
                metrics=self._metric_objects,
                llm=self._llm,
                embeddings=self._embeddings,
                raise_exceptions=False,
                show_progress=False,
            )

        result = await loop.run_in_executor(None, _run)
        sub_metrics = _extract_scores(result, self._spec.metrics)
        raw_calls = [
            JudgeCallRecord(
                judge_model=f"ragas:{name}",
                sub_run_index=idx,
                position=None,
                score=value,
                winner=None,
                reason=None,
                raw={"metric": name, "value": value},
            )
            for idx, (name, value) in enumerate(sub_metrics.items())
        ]
        return sub_metrics, raw_calls


def _build_metrics(names: list[str], llm: Any, embeddings: Any) -> list[Any]:
    """Instantiate ragas metric objects with our LLM/embeddings injected.

    ragas 0.1.x exposes module-level ``faithfulness`` etc. as singletons
    that need ``.llm`` / ``.embeddings`` set; 0.2+ exposes constructable
    classes. We try the class path first and fall back to the singleton
    pattern. Either way the instances we hand to ``evaluate`` are
    self-contained and don't read any global config.
    """
    from ragas.metrics import (
        answer_relevancy,
        context_precision,
        faithfulness,
    )

    name_to_obj = {
        "faithfulness": faithfulness,
        "context_precision": context_precision,
        "answer_relevance": answer_relevancy,
    }
    out: list[Any] = []
    for name in names:
        base = name_to_obj.get(name)
        if base is None:
            raise ValueError(f"unknown ragas metric: {name}")
        # ragas singletons: mutate to bind our llm/embeddings. Some metric
        # objects don't have an ``embeddings`` slot — silently skip rather
        # than fork the per-metric setup logic.
        with contextlib.suppress(AttributeError):
            base.llm = llm
        with contextlib.suppress(AttributeError):
            base.embeddings = embeddings
        out.append(base)
    return out


def _extract_scores(result: Any, requested: list[str]) -> dict[str, float]:
    """ragas's ``EvaluationResult`` exposes per-metric means. Across the
    0.1/0.2 boundary the access shape changed (mapping vs. attribute),
    so try several shapes and clamp."""
    name_aliases = {
        "faithfulness": ("faithfulness",),
        "context_precision": ("context_precision",),
        "answer_relevance": ("answer_relevancy", "answer_relevance"),
    }
    sub: dict[str, float] = {}
    # mapping-style: result["faithfulness"]
    for canonical in requested:
        value: float | None = None
        for alias in name_aliases.get(canonical, (canonical,)):
            try:
                v = result[alias]  # type: ignore[index]
            except (KeyError, TypeError, IndexError):
                v = None
            if v is None:
                v = getattr(result, alias, None)
            if v is None and hasattr(result, "to_pandas"):
                try:
                    df = result.to_pandas()
                    if alias in df.columns:
                        v = float(df[alias].mean())
                except Exception:  # pragma: no cover — best-effort fallback
                    v = None
            if v is not None:
                value = float(v) if not isinstance(v, list) else float(sum(v) / max(1, len(v)))
                break
        sub[canonical] = _clamp(value if value is not None else 0.0)
    return sub


def _clamp(v: float) -> float:
    if v is None or math.isnan(v):
        return 0.0
    return max(0.0, min(1.0, float(v)))


def _mean_clamped(values: Any) -> float:
    vals = [_clamp(float(v)) for v in values]
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def _confidence_from_sub_metrics(sub_metrics: dict[str, float]) -> float:
    """Confidence proxy: 1 - normalised stdev across metrics. When all
    three RAG metrics agree, we trust the score; when they diverge
    (e.g. perfect faithfulness but garbage context_precision), confidence
    drops. Cheap and matches the MultiJudge cross-judge spread term."""
    if len(sub_metrics) < 2:
        return 1.0
    vals = list(sub_metrics.values())
    mean = sum(vals) / len(vals)
    variance = sum((v - mean) ** 2 for v in vals) / len(vals)
    std = variance**0.5
    return max(0.0, 1.0 - (std / 0.5))
