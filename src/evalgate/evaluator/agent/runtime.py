"""Planner + tool execution loop for task_type=agent cases."""

from __future__ import annotations

import time
from typing import Any

import litellm

from evalgate.evaluator.agent.parser import ActionParseError, parse_action
from evalgate.evaluator.agent.tools import BuiltinToolRegistry
from evalgate.evaluator.agent.types import AgentRuntimeResult, TrajectoryStep
from evalgate.judge.prompt_spec import PromptSpec
from evalgate.judge.protocol import acompletion_json

_ACTION_SCHEMA = (
    'Respond with STRICT JSON only. Either {"action":"call_tool","tool":"<name>",'
    '"args":{...}} or {"action":"final_answer","answer":"..."}'
)


class AgentRuntime:
    def __init__(self, spec: PromptSpec, *, mock: bool = False):
        if spec.agent_runtime is None:
            raise ValueError("AgentRuntime requires prompt.agent_runtime")
        self._spec = spec
        self._runtime = spec.agent_runtime
        self._registry = BuiltinToolRegistry.from_names(self._runtime.tool_names)
        self._mock = mock

    @property
    def planner_model(self) -> str:
        return self._runtime.planner_model or self._spec.candidate.model

    async def run(self, case_input: dict[str, Any], *, mock: bool = False) -> AgentRuntimeResult:
        use_mock = mock or self._mock
        messages = _bootstrap_messages(self._spec, case_input, allowed_tools=self._registry.names)
        steps: list[TrajectoryStep] = []
        llm_calls: list[dict[str, Any]] = []
        total_cost = 0.0
        total_latency_ms = 0

        for step_idx in range(self._runtime.max_steps):
            mock_response = _mock_action(step_idx, self._registry.names) if use_mock else None
            t0 = time.perf_counter()
            text, raw = await acompletion_json(
                model=self.planner_model,
                messages=messages,
                params=self._spec.candidate.params,
                mock_response=mock_response,
            )
            total_latency_ms += int((time.perf_counter() - t0) * 1000)
            total_cost += _safe_cost(raw)
            llm_calls.append({"step_idx": step_idx, "response_text": text, "raw": raw})
            messages.append({"role": "assistant", "content": text})

            try:
                action = parse_action(text)
            except ActionParseError as exc:
                return AgentRuntimeResult(
                    final_answer="",
                    steps=steps,
                    llm_calls=llm_calls,
                    stopped_reason=f"action_parse_error: {exc}",
                    cost_usd=total_cost,
                    latency_ms=total_latency_ms,
                )

            if action.action == "final_answer":
                return AgentRuntimeResult(
                    final_answer=action.answer or "",
                    steps=steps,
                    llm_calls=llm_calls,
                    cost_usd=total_cost,
                    latency_ms=total_latency_ms,
                )

            assert action.tool is not None
            tool_error: str | None = None
            observation: Any
            try:
                observation = await self._registry.run(action.tool, action.args)
            except Exception as exc:  # tool errors are part of trajectory quality.
                observation = {"error": str(exc)}
                tool_error = str(exc)
            steps.append(
                TrajectoryStep(
                    tool=action.tool,
                    args=dict(action.args),
                    observation=observation,
                    error=tool_error,
                )
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "TOOL_OBSERVATION:\n"
                        f'{{"tool":"{action.tool}","observation":{_json_dump(observation)}}}\n'
                        "Now choose the next action."
                    ),
                }
            )

        return AgentRuntimeResult(
            final_answer="",
            steps=steps,
            llm_calls=llm_calls,
            stopped_reason=f"max_steps_exceeded:{self._runtime.max_steps}",
            cost_usd=total_cost,
            latency_ms=total_latency_ms,
        )


def _safe_cost(raw: Any) -> float:
    """Best-effort per-call cost; 0.0 for mock / un-priced models (mirrors
    ``judge.candidate._safe_cost`` but reads the serialized response dict)."""
    if not raw or not isinstance(raw, dict) or raw.get("error"):
        return 0.0
    try:
        return float(litellm.completion_cost(completion_response=raw) or 0.0)
    except Exception:
        return 0.0


def _bootstrap_messages(
    spec: PromptSpec, case_input: dict[str, Any], *, allowed_tools: list[str]
) -> list[dict[str, str]]:
    messages = spec.render_messages(case_input)
    tool_line = ", ".join(allowed_tools)
    instruction = (
        f"{_ACTION_SCHEMA}\n"
        f"Allowed tools: [{tool_line}].\n"
        "Use only listed tools. Keep args as a JSON object."
    )
    messages.append({"role": "user", "content": instruction})
    return messages


def _mock_action(step_idx: int, tool_names: list[str]) -> str:
    if step_idx == 0 and tool_names:
        return f'{{"action":"call_tool","tool":"{tool_names[0]}","args":{{}}}}'
    if step_idx == 1 and len(tool_names) > 1:
        return f'{{"action":"call_tool","tool":"{tool_names[1]}","args":{{}}}}'
    return '{"action":"final_answer","answer":"mock-final-answer"}'


def _json_dump(value: Any) -> str:
    import json

    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return json.dumps({"repr": repr(value)}, ensure_ascii=False, sort_keys=True)
