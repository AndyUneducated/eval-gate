"""Phase 8: ``evalgate run`` end-to-end on a RAG eval set, with ragas stubbed.

We seed a tiny RAG eval set, drive the runner directly (the CLI is just
a thin wrapper), and stub the ragas scorer + candidate call so the test
doesn't need a live model. Then assert the records dict carries
``axis_breakdown["quality"]`` and feeds straight into ``build_gate_report``
to surface the nested quality breakdown.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evalgate.core.schemas import TaskKind
from evalgate.eval_set import repository
from evalgate.evaluator import runner as eval_runner
from evalgate.gate.decision import build_gate_report
from evalgate.judge.candidate import CandidateOutput
from evalgate.judge.protocol import JudgeCallRecord


def _corpus(tmp_path: Path) -> Path:
    p = tmp_path / "corpus.json"
    p.write_text(
        json.dumps(
            [
                {"id": "a", "text": "Acme bills monthly. Invoices are due 14 days later."},
                {"id": "b", "text": "Refunds within 5-10 business days."},
            ]
        )
    )
    return p


def _yaml(tmp_path: Path, corpus_path: Path) -> Path:
    p = tmp_path / "rag.yaml"
    p.write_text(
        f"""\
name: rag-cli-test
candidate:
  model: ollama/qwen3.5:9b
  user_template: "Context:\\n{{contexts}}\\n\\nQuestion: {{question}}"
  params: {{}}
judges:
  - model: ollama/qwen3.5:9b
    rubric: "rate"
    params: {{}}
judge_policy:
  mode: pointwise
  k: 1
  position_swap: false
  concurrency: 2
retriever:
  kind: embedding
  corpus_path: {corpus_path}
  embedding_model: ollama/qwen3-embedding:8b
  top_k: 2
rag_evaluator:
  llm_model: ollama/qwen3.5:9b
  embedding_model: ollama/qwen3-embedding:8b
  metrics:
    - faithfulness
    - context_precision
    - answer_relevance
"""
    )
    return p


async def _seed(db_session_factory) -> str:
    async with db_session_factory() as session:
        s = await repository.create_eval_set(session, name="rag-cli-test")
        for i in range(3):
            await repository.add_case(
                session,
                set_id=s.id,
                task_type=TaskKind.rag,
                input={"question": f"q{i}"},
                expected={"answer": f"a{i}"},
                retrieved_contexts=[f"context for q{i}"],
                tags=["billing"],
            )
        return s.id


def _stub_ragas(monkeypatch, sub_metric_values: dict[str, float]):
    """Patch the candidate call AND every RagEvaluator's scorer."""

    async def fake_run_candidate(case_input, _spec, *, mock_response=None):
        return CandidateOutput(
            text=f"answer to {case_input.get('question', '?')}",
            latency_ms=10,
            cost_usd=0.0,
            raw={},
        )

    monkeypatch.setattr(
        "evalgate.evaluator.rag.evaluator.run_candidate",
        fake_run_candidate,
    )

    async def fake_score(self, **kwargs):
        calls = [
            JudgeCallRecord(
                judge_model=f"ragas:{name}",
                sub_run_index=i,
                score=value,
                raw={"metric": name, "value": value},
            )
            for i, (name, value) in enumerate(sub_metric_values.items())
        ]
        return dict(sub_metric_values), calls

    monkeypatch.setattr(
        "evalgate.evaluator.rag.evaluator._RagasScorer.score",
        fake_score,
    )


@pytest.mark.asyncio
async def test_run_eval_rag_emits_axis_breakdown_and_feeds_gate(
    monkeypatch, db_session_factory, tmp_path: Path
):
    corpus_path = _corpus(tmp_path)
    yaml_path = _yaml(tmp_path, corpus_path)
    set_id = await _seed(db_session_factory)

    # Baseline run.
    _stub_ragas(
        monkeypatch,
        {"faithfulness": 0.9, "context_precision": 0.85, "answer_relevance": 0.95},
    )
    async with db_session_factory() as session:
        baseline_result = await eval_runner.run_eval(
            session,
            eval_set_id_or_name=set_id,
            prompt_path=str(yaml_path),
            mock=True,
        )

    # Candidate run — faithfulness collapses on every case.
    _stub_ragas(
        monkeypatch,
        {"faithfulness": 0.05, "context_precision": 0.85, "answer_relevance": 0.95},
    )
    async with db_session_factory() as session:
        candidate_result = await eval_runner.run_eval(
            session,
            eval_set_id_or_name=set_id,
            prompt_path=str(yaml_path),
            mock=True,
        )

    baseline = [r.model_dump() for r in baseline_result.records]
    candidate = [r.model_dump() for r in candidate_result.records]

    assert all(
        set((r["axis_breakdown"] or {}).get("quality") or {})
        == {"faithfulness", "context_precision", "answer_relevance"}
        for r in baseline + candidate
    )

    # Mean score = mean of sub-metrics, so baseline > candidate by design.
    assert (baseline_result.mean_score or 0) > (candidate_result.mean_score or 0)

    report = build_gate_report(baseline, candidate)
    quality = next(a for a in report.axes if a.name == "quality")
    assert quality.sub_metrics is not None
    assert set(quality.sub_metrics) == {
        "faithfulness",
        "context_precision",
        "answer_relevance",
    }
    assert quality.sub_metrics["faithfulness"].delta < 0
    assert quality.sub_metrics["context_precision"].delta == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_run_eval_rag_persists_axis_breakdown_and_retrieved_contexts(
    monkeypatch, db_session_factory, tmp_path: Path
):
    """Side-effect contract: eval_results rows carry ``axis_breakdown`` (with
    a ``quality`` bucket) + the runtime retrieved_contexts the candidate
    consumed."""
    from evalgate.judge import persistence as judge_repo

    corpus_path = _corpus(tmp_path)
    yaml_path = _yaml(tmp_path, corpus_path)
    set_id = await _seed(db_session_factory)

    _stub_ragas(
        monkeypatch,
        {"faithfulness": 0.7, "context_precision": 0.6, "answer_relevance": 0.8},
    )
    async with db_session_factory() as session:
        run_result = await eval_runner.run_eval(
            session,
            eval_set_id_or_name=set_id,
            prompt_path=str(yaml_path),
            mock=True,
        )

    async with db_session_factory() as session:
        results = await judge_repo.list_results(session, run_result.run_id)

    assert len(results) == 3
    for r in results:
        assert isinstance(r.axis_breakdown, dict)
        assert r.axis_breakdown["quality"] == {
            "faithfulness": 0.7,
            "context_precision": 0.6,
            "answer_relevance": 0.8,
        }
        # Phase 10 safety pipeline runs on the candidate output too — clean
        # ragas-mock answers contain no PII / jailbreak signals, so all four
        # rates are 0.0 but the bucket still exists.
        assert set(r.axis_breakdown["safety"]) == {
            "pii_input_rate",
            "pii_output_leak_rate",
            "jailbreak_attempt_rate",
            "jailbreak_compliance_rate",
        }
        assert all(v == 0.0 for v in r.axis_breakdown["safety"].values())
        assert isinstance(r.retrieved_contexts, list)
        assert len(r.retrieved_contexts) == 2  # top_k = 2 from YAML
