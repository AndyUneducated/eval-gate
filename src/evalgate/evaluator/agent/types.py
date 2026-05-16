"""Shared datatypes for the Phase 9 agent runtime/evaluator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ActionKind = Literal["call_tool", "final_answer"]


@dataclass
class ParsedAction:
    action: ActionKind
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    answer: str | None = None


@dataclass
class TrajectoryStep:
    """One executed tool step in the runtime trajectory."""

    tool: str
    args: dict[str, Any]
    observation: Any
    error: str | None = None


@dataclass
class AgentRuntimeResult:
    """Execution output consumed by AgentTrajectoryEvaluator."""

    final_answer: str
    steps: list[TrajectoryStep]
    llm_calls: list[dict[str, Any]]
    stopped_reason: str | None = None
