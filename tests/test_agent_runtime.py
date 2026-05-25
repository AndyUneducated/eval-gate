from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from evalgate.evaluator.agent.runtime import AgentRuntime
from evalgate.judge.prompt_spec import (
    AgentRuntimeSpec,
    CandidateSpec,
    JudgePolicySpec,
    JudgeSpec,
    PromptSpec,
)


def _spec(*, max_steps: int = 4, tool_names: list[str] | None = None) -> PromptSpec:
    return PromptSpec(
        name="agent-runtime-test",
        candidate=CandidateSpec(model="ollama/qwen3.5:9b", user_template="{question}"),
        judges=[JudgeSpec(model="ollama/qwen3.5:9b", rubric="rate")],
        judge_policy=JudgePolicySpec(mode="pointwise"),
        agent_runtime=AgentRuntimeSpec(
            max_steps=max_steps,
            tool_names=tool_names or ["lookup_invoice", "fetch_policy"],
        ),
    )


@pytest.mark.asyncio
async def test_mock_runtime_executes_two_steps_then_finishes():
    runtime = AgentRuntime(_spec(), mock=True)
    result = await runtime.run({"question": "q"})
    assert result.final_answer == "mock-final-answer"
    assert result.stopped_reason is None
    assert [s.tool for s in result.steps] == ["lookup_invoice", "fetch_policy"]
    assert len(result.llm_calls) == 3  # two tools + final answer


@pytest.mark.asyncio
async def test_runtime_respects_max_steps():
    runtime = AgentRuntime(_spec(max_steps=1), mock=True)
    result = await runtime.run({"question": "q"})
    assert result.final_answer == ""
    assert len(result.steps) == 1
    assert result.stopped_reason is not None
    assert result.stopped_reason.startswith("max_steps_exceeded")


@pytest.mark.asyncio
async def test_runtime_returns_parse_error_when_action_invalid_json(monkeypatch):
    async def bad_action(*, model, messages, params=None, mock_response=None):
        return "not-json", {"raw": "bad"}

    monkeypatch.setattr(
        "evalgate.evaluator.agent.runtime.acompletion_json",
        AsyncMock(side_effect=bad_action),
    )
    runtime = AgentRuntime(_spec(), mock=False)
    result = await runtime.run({"question": "q"})
    assert result.final_answer == ""
    assert result.steps == []
    assert result.stopped_reason is not None
    assert "action_parse_error" in result.stopped_reason
