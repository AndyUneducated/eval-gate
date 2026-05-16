"""Phase 8: RagEvaluator end-to-end with ragas.evaluate stubbed.

We don't exercise real ragas in unit tests — ragas's own LLM calls
would either need a live model or the LiteLLM mock_text path that the
adapter already covers. Instead we patch the scorer at its boundary so
the test focuses on:

- the retriever→candidate→ragas chain runs in order,
- the ragas dict turns into an EvaluationOutcome with ``axis_breakdown``
  + composite score (mean) + a JudgeCallRecord per metric,
- failure modes (retrieve / candidate / ragas) become per-case error
  outcomes rather than raising.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from evalgate.db.models import EvalCaseRow
from evalgate.evaluator.rag.evaluator import RagEvaluator
from evalgate.judge.prompt_spec import (
    CandidateSpec,
    JudgePolicySpec,
    JudgeSpec,
    PromptSpec,
    RagEvaluatorSpec,
    RetrieverSpec,
)


@pytest.fixture
def corpus_path(tmp_path: Path) -> Path:
    p = tmp_path / "corpus.json"
    p.write_text(
        json.dumps(
            [
                {"id": "x", "text": "Acme bills monthly. Invoices are due 14 days later."},
                {"id": "y", "text": "Refunds within 5-10 business days."},
            ]
        )
    )
    return p


def _spec(corpus_path: Path) -> PromptSpec:
    return PromptSpec(
        name="rag-test",
        candidate=CandidateSpec(
            model="ollama/qwen2.5:7b",
            user_template="Context:\n{contexts}\n\nQuestion: {question}",
        ),
        judges=[JudgeSpec(model="ollama/qwen2.5:7b", rubric="rate")],
        judge_policy=JudgePolicySpec(mode="pointwise"),
        retriever=RetrieverSpec(
            kind="embedding",
            corpus_path=str(corpus_path),
            embedding_model="ollama/qwen3-embedding:8b",
            top_k=2,
        ),
        rag_evaluator=RagEvaluatorSpec(
            llm_model="ollama/qwen2.5:7b",
            embedding_model="ollama/qwen3-embedding:8b",
        ),
    )


def _case() -> EvalCaseRow:
    row = EvalCaseRow(
        id="case-1",
        task_type="rag",
        input={"question": "when are invoices due?"},
        expected={"answer": "14 days"},
        tags=["billing"],
        retrieved_contexts=["Acme bills monthly. Invoices are due 14 days later."],
    )
    return row


@pytest.mark.asyncio
async def test_rag_evaluator_happy_path(monkeypatch, corpus_path: Path):
    spec = _spec(corpus_path)
    evaluator = RagEvaluator(spec, mock=True)

    # Stub the candidate LLM call so we don't go through litellm at all.
    async def fake_run_candidate(case_input, _spec, *, mock_response=None):
        from evalgate.judge.candidate import CandidateOutput

        assert "{contexts}" not in case_input.get("question", "")
        assert "contexts" in case_input
        return CandidateOutput(
            text="Invoices are due 14 days after issuance.",
            latency_ms=42,
            cost_usd=0.0,
            raw={},
        )

    monkeypatch.setattr(
        "evalgate.evaluator.rag.evaluator.run_candidate",
        fake_run_candidate,
    )

    # Stub the ragas scorer so we don't import / drive real ragas.
    async def fake_score(**kwargs):
        from evalgate.judge.protocol import JudgeCallRecord

        sub = {
            "faithfulness": 0.8,
            "context_precision": 0.6,
            "answer_relevance": 1.0,
        }
        calls = [
            JudgeCallRecord(
                judge_model=f"ragas:{name}",
                sub_run_index=i,
                score=value,
                raw={"metric": name, "value": value},
            )
            for i, (name, value) in enumerate(sub.items())
        ]
        return sub, calls

    monkeypatch.setattr(evaluator._scorer, "score", fake_score)

    outcome = await evaluator.evaluate(_case())
    assert not outcome.error
    assert outcome.output_text.startswith("Invoices are due 14 days")
    assert outcome.latency_ms == 42
    assert outcome.axis_breakdown == {
        "quality": {
            "faithfulness": 0.8,
            "context_precision": 0.6,
            "answer_relevance": 1.0,
        }
    }
    assert outcome.score == pytest.approx((0.8 + 0.6 + 1.0) / 3)
    assert outcome.retrieved_contexts is not None
    assert len(outcome.retrieved_contexts) == 2  # top_k from corpus
    assert len(outcome.raw_calls) == 3
    assert {c.judge_model for c in outcome.raw_calls} == {
        "ragas:faithfulness",
        "ragas:context_precision",
        "ragas:answer_relevance",
    }


@pytest.mark.asyncio
async def test_rag_evaluator_candidate_failure_yields_error_outcome(monkeypatch, corpus_path: Path):
    evaluator = RagEvaluator(_spec(corpus_path), mock=True)

    async def boom(case_input, _spec, *, mock_response=None):
        raise RuntimeError("ollama unreachable")

    monkeypatch.setattr("evalgate.evaluator.rag.evaluator.run_candidate", boom)

    outcome = await evaluator.evaluate(_case())
    assert outcome.error is True
    assert outcome.error_kind == "candidate_failure"
    assert "candidate-failed" in (outcome.reason or "")
    # We still record the contexts the retriever produced for audit.
    assert outcome.retrieved_contexts is not None


@pytest.mark.asyncio
async def test_rag_evaluator_ragas_failure_yields_error_outcome(monkeypatch, corpus_path: Path):
    evaluator = RagEvaluator(_spec(corpus_path), mock=True)

    async def fake_run_candidate(case_input, _spec, *, mock_response=None):
        from evalgate.judge.candidate import CandidateOutput

        return CandidateOutput(text="ans", latency_ms=1, cost_usd=0.0, raw={})

    monkeypatch.setattr("evalgate.evaluator.rag.evaluator.run_candidate", fake_run_candidate)
    monkeypatch.setattr(
        evaluator._scorer,
        "score",
        AsyncMock(side_effect=RuntimeError("ragas internal explosion")),
    )

    outcome = await evaluator.evaluate(_case())
    assert outcome.error is True
    assert outcome.error_kind == "ragas_failure"
    assert outcome.score == 0.0
    assert outcome.output_text == "ans"  # we still keep the candidate's output


@pytest.mark.asyncio
async def test_rag_evaluator_requires_both_blocks():
    spec = PromptSpec(
        name="x",
        candidate=CandidateSpec(model="ollama/qwen2.5:7b"),
        judges=[JudgeSpec(model="ollama/qwen2.5:7b", rubric="r")],
        judge_policy=JudgePolicySpec(mode="pointwise"),
    )
    with pytest.raises(ValueError):
        RagEvaluator(spec, mock=True)
