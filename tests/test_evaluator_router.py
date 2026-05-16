"""Phase 8: EvaluatorRouter dispatch + build_router behaviour.

These tests exercise the registry in isolation — no real evaluators run.
We register stub evaluators per task type and assert dispatch picks the
right one (or raises) when handed cases of the corresponding type.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from evalgate.core.schemas import TaskKind
from evalgate.evaluator.base import EvaluationOutcome, UnsupportedTaskTypeError
from evalgate.evaluator.router import EvaluatorRouter, build_router
from evalgate.judge.prompt_spec import (
    AgentRuntimeSpec,
    CandidateSpec,
    JudgePolicySpec,
    JudgeSpec,
    PromptSpec,
    RagEvaluatorSpec,
    RetrieverSpec,
)


@dataclass
class _StubEvaluator:
    label: str

    async def evaluate(self, case, *, mock=False):  # pragma: no cover - never called
        return EvaluationOutcome(score=1.0, output_text="", cost_usd=0, latency_ms=0)


@dataclass
class _StubCase:
    id: str
    task_type: str


def _generic_spec() -> PromptSpec:
    return PromptSpec(
        name="t",
        candidate=CandidateSpec(model="ollama/qwen2.5:7b"),
        judges=[JudgeSpec(model="ollama/qwen2.5:7b", rubric="rate")],
        judge_policy=JudgePolicySpec(mode="pointwise"),
    )


def _rag_spec() -> PromptSpec:
    return PromptSpec(
        name="t",
        candidate=CandidateSpec(model="ollama/qwen2.5:7b"),
        judges=[JudgeSpec(model="ollama/qwen2.5:7b", rubric="rate")],
        judge_policy=JudgePolicySpec(mode="pointwise"),
        retriever=RetrieverSpec(
            kind="embedding",
            corpus_path="examples/rag_demo/corpus.json",
            embedding_model="ollama/qwen3-embedding:8b",
        ),
        rag_evaluator=RagEvaluatorSpec(
            llm_model="ollama/qwen2.5:7b",
            embedding_model="ollama/qwen3-embedding:8b",
        ),
    )


def _agent_spec() -> PromptSpec:
    return PromptSpec(
        name="t",
        candidate=CandidateSpec(model="ollama/qwen2.5:7b"),
        judges=[JudgeSpec(model="ollama/qwen2.5:7b", rubric="rate")],
        judge_policy=JudgePolicySpec(mode="pointwise"),
        agent_runtime=AgentRuntimeSpec(
            max_steps=4,
            tool_names=["lookup_invoice", "fetch_policy"],
        ),
    )


def test_router_dispatch_by_task_type():
    g = _StubEvaluator(label="g")
    r = _StubEvaluator(label="r")
    router = EvaluatorRouter({TaskKind.generic: g, TaskKind.rag: r})

    assert router.for_case(_StubCase("c1", "generic")) is g
    assert router.for_case(_StubCase("c2", "rag")) is r


def test_router_unknown_task_type_raises():
    router = EvaluatorRouter({TaskKind.generic: _StubEvaluator("g")})
    with pytest.raises(UnsupportedTaskTypeError):
        router.for_case(_StubCase("c1", "agent"))


def test_router_rejects_invalid_task_type():
    router = EvaluatorRouter({TaskKind.generic: _StubEvaluator("g")})
    with pytest.raises(UnsupportedTaskTypeError):
        router.for_case(_StubCase("c1", "not-a-real-type"))


def test_router_label_joins_evaluator_labels_in_taskkind_order():
    router = EvaluatorRouter(
        {
            TaskKind.rag: _StubEvaluator(label="ragas"),
            TaskKind.generic: _StubEvaluator(label="ollama/qwen"),
        }
    )
    # TaskKind values: agent / generic / rag (alphabetical order)
    assert router.label() == "ollama/qwen+ragas"


def test_build_router_generic_only_when_rag_blocks_missing():
    router = build_router(_generic_spec(), mock=True)
    assert router.registered == {TaskKind.generic}


def test_build_router_registers_rag_when_blocks_present():
    router = build_router(_rag_spec(), mock=True)
    assert router.registered == {TaskKind.generic, TaskKind.rag}


def test_build_router_registers_agent_when_block_present():
    router = build_router(_agent_spec(), mock=True)
    assert router.registered == {TaskKind.generic, TaskKind.agent}


def test_router_rejects_empty_registry():
    with pytest.raises(ValueError):
        EvaluatorRouter({})
