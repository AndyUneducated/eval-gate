"""Task-type=agent evaluator: runtime trajectory vs expected trajectory."""

from __future__ import annotations

from typing import Any

from evalgate.db.models import EvalCaseRow
from evalgate.evaluator.agent.runtime import AgentRuntime
from evalgate.evaluator.base import EvaluationOutcome
from evalgate.judge.prompt_spec import PromptSpec
from evalgate.judge.protocol import JudgeCallRecord


class AgentTrajectoryEvaluator:
    label = "agent-runtime"

    def __init__(self, spec: PromptSpec, *, mock: bool = False):
        self._runtime = AgentRuntime(spec, mock=mock)
        self._mock = mock

    async def evaluate(self, case: EvalCaseRow, *, mock: bool = False) -> EvaluationOutcome:
        expected = _normalise_expected(case.expected_trajectory)
        if not expected:
            return EvaluationOutcome(
                score=0.0,
                output_text="",
                cost_usd=0.0,
                latency_ms=0,
                sub_metrics={"tool_call_accuracy": 0.0, "step_wise_success": 0.0},
                judge_raw={"error": "missing_expected_trajectory"},
                reason="agent case requires non-empty expected_trajectory",
                error=True,
                error_kind="missing_expected_trajectory",
            )

        case_input = dict(case.input or {})
        run = await self._runtime.run(case_input, mock=(mock or self._mock))
        actual = [
            {
                "tool": step.tool,
                "args": dict(step.args or {}),
                "error": step.error,
                "observation": step.observation,
            }
            for step in run.steps
        ]

        match_flags: list[bool] = []
        mismatch_reasons: list[str] = []
        for idx, exp in enumerate(expected):
            if idx >= len(actual):
                match_flags.append(False)
                mismatch_reasons.append(
                    f"step[{idx}] missing actual tool call; expected tool={exp['tool']!r}"
                )
                continue
            act = actual[idx]
            name_ok = exp["tool"] == act["tool"]
            args_ok = _args_subset(exp.get("args", {}), act.get("args", {}))
            ok = name_ok and args_ok
            match_flags.append(ok)
            if not ok:
                mismatch_reasons.append(
                    f"step[{idx}] mismatch: expected {exp!r}, actual tool={act['tool']!r} "
                    f"args={act.get('args', {})!r}"
                )

        n = max(1, len(expected))
        tool_call_accuracy = sum(1 for m in match_flags if m) / n
        matched_prefix = 0
        for flag in match_flags:
            if flag:
                matched_prefix += 1
                continue
            break
        step_wise_success = matched_prefix / n
        score = (tool_call_accuracy + step_wise_success) / 2.0

        raw_calls: list[JudgeCallRecord] = []
        for idx, step in enumerate(actual):
            score_i = 1.0 if idx < len(match_flags) and match_flags[idx] else 0.0
            raw_calls.append(
                JudgeCallRecord(
                    judge_model="agent-runtime",
                    sub_run_index=idx,
                    position=None,
                    score=score_i,
                    winner=None,
                    reason=None if score_i else mismatch_reasons[0] if mismatch_reasons else None,
                    raw={
                        "tool": step["tool"],
                        "args": step.get("args", {}),
                        "observation": step.get("observation"),
                        "error": step.get("error"),
                    },
                )
            )

        judge_raw = {
            "expected_trajectory": expected,
            "actual_trajectory": actual,
            "mismatch_reasons": mismatch_reasons,
            "stopped_reason": run.stopped_reason,
        }
        runtime_error = run.stopped_reason is not None
        return EvaluationOutcome(
            score=score,
            output_text=run.final_answer,
            cost_usd=0.0,
            latency_ms=0,
            confidence=step_wise_success,
            sub_metrics={
                "tool_call_accuracy": tool_call_accuracy,
                "step_wise_success": step_wise_success,
            },
            raw_calls=raw_calls,
            judge_raw=judge_raw,
            reason=run.stopped_reason,
            error=runtime_error,
            error_kind="agent_runtime_error" if runtime_error else None,
        )


def _normalise_expected(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for idx, raw in enumerate(value):
        if not isinstance(raw, dict):
            continue
        tool = raw.get("tool")
        args = raw.get("args", {})
        if not isinstance(tool, str) or not tool.strip():
            continue
        if not isinstance(args, dict):
            continue
        out.append({"tool": tool.strip(), "args": args, "_idx": idx})
    return out


def _args_subset(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    """Return True when expected JSON object is a deep subset of actual."""
    for key, exp_value in expected.items():
        if key not in actual:
            return False
        act_value = actual[key]
        if isinstance(exp_value, dict):
            if not isinstance(act_value, dict):
                return False
            if not _args_subset(exp_value, act_value):
                return False
            continue
        if isinstance(exp_value, list):
            if not isinstance(act_value, list):
                return False
            if len(exp_value) > len(act_value):
                return False
            for idx, item in enumerate(exp_value):
                if isinstance(item, dict):
                    if not isinstance(act_value[idx], dict):
                        return False
                    if not _args_subset(item, act_value[idx]):
                        return False
                    continue
                if item != act_value[idx]:
                    return False
            continue
        if exp_value != act_value:
            return False
    return True
