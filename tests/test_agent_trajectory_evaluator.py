from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from evalgate.evaluator.agent.evaluator import AgentTrajectoryEvaluator
from evalgate.evaluator.agent.types import AgentRuntimeResult, TrajectoryStep
from evalgate.judge.prompt_spec import (
    AgentRuntimeSpec,
    CandidateSpec,
    JudgePolicySpec,
    JudgeSpec,
    PromptSpec,
)


def _spec() -> PromptSpec:
    return PromptSpec(
        name="agent-evaluator-test",
        candidate=CandidateSpec(model="ollama/qwen2.5:7b", user_template="{question}"),
        judges=[JudgeSpec(model="ollama/qwen2.5:7b", rubric="rate")],
        judge_policy=JudgePolicySpec(mode="pointwise"),
        agent_runtime=AgentRuntimeSpec(
            max_steps=6, tool_names=["lookup_invoice", "fetch_policy", "get_payment_attempts"]
        ),
    )


@dataclass
class _Case:
    id: str = "case-1"
    task_type: str = "agent"
    input: dict = field(default_factory=lambda: {"question": "q"})
    expected_trajectory: list[dict] = field(default_factory=list)


@pytest.mark.asyncio
async def test_missing_expected_trajectory_is_error():
    evaluator = AgentTrajectoryEvaluator(_spec(), mock=True)
    out = await evaluator.evaluate(_Case(expected_trajectory=[]))
    assert out.error is True
    assert out.error_kind == "missing_expected_trajectory"
    assert out.sub_metrics == {"tool_call_accuracy": 0.0, "step_wise_success": 0.0}


@pytest.mark.asyncio
async def test_perfect_match_scores_one(monkeypatch):
    evaluator = AgentTrajectoryEvaluator(_spec(), mock=False)
    monkeypatch.setattr(
        evaluator._runtime,
        "run",
        AsyncMock(
            return_value=AgentRuntimeResult(
                final_answer="ok",
                steps=[
                    TrajectoryStep("lookup_invoice", {"invoice_id": "INV-42", "debug": True}, {}),
                    TrajectoryStep("fetch_policy", {"topic": "billing"}, {}),
                ],
                llm_calls=[],
            )
        ),
    )
    case = _Case(
        expected_trajectory=[
            {"tool": "lookup_invoice", "args": {"invoice_id": "INV-42"}},
            {"tool": "fetch_policy", "args": {"topic": "billing"}},
        ]
    )
    out = await evaluator.evaluate(case)
    assert out.error is False
    assert out.score == pytest.approx(1.0)
    assert out.sub_metrics == {"tool_call_accuracy": 1.0, "step_wise_success": 1.0}
    assert out.output_text == "ok"


@pytest.mark.asyncio
async def test_middle_step_mismatch_drops_step_wise_success(monkeypatch):
    evaluator = AgentTrajectoryEvaluator(_spec(), mock=False)
    monkeypatch.setattr(
        evaluator._runtime,
        "run",
        AsyncMock(
            return_value=AgentRuntimeResult(
                final_answer="final-but-wrong-path",
                steps=[
                    TrajectoryStep("lookup_invoice", {"invoice_id": "INV-42"}, {}),
                    TrajectoryStep("get_payment_attempts", {"invoice_id": "INV-42"}, {}),
                ],
                llm_calls=[],
            )
        ),
    )
    case = _Case(
        expected_trajectory=[
            {"tool": "lookup_invoice", "args": {"invoice_id": "INV-42"}},
            {"tool": "fetch_policy", "args": {"topic": "billing"}},
        ]
    )
    out = await evaluator.evaluate(case)
    assert out.error is False
    assert out.sub_metrics is not None
    assert out.sub_metrics["tool_call_accuracy"] == pytest.approx(0.5)
    assert out.sub_metrics["step_wise_success"] == pytest.approx(0.5)
    assert out.score == pytest.approx(0.5)
    assert out.judge_raw is not None
    assert out.judge_raw["mismatch_reasons"]
