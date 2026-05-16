"""Parse strict JSON agent actions from planner outputs."""

from __future__ import annotations

import json
from typing import Any

from evalgate.evaluator.agent.types import ParsedAction


class ActionParseError(ValueError):
    """Raised when planner output is not a valid action payload."""


def parse_action(text: str) -> ParsedAction:
    """Parse planner output into a strict action contract.

    Supported payloads:
    - {"action":"call_tool","tool":"...","args":{...}}
    - {"action":"final_answer","answer":"..."}
    """
    if not text or not text.strip():
        raise ActionParseError("planner returned empty action payload")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ActionParseError(f"planner output is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ActionParseError("planner output must be a JSON object")

    action = payload.get("action")
    if action == "call_tool":
        tool = payload.get("tool")
        if not isinstance(tool, str) or not tool.strip():
            raise ActionParseError("call_tool action requires non-empty string `tool`")
        args = payload.get("args", {})
        if not isinstance(args, dict):
            raise ActionParseError("call_tool action requires object `args`")
        return ParsedAction(action="call_tool", tool=tool.strip(), args=args)
    if action == "final_answer":
        answer = payload.get("answer")
        if not isinstance(answer, str):
            raise ActionParseError("final_answer action requires string `answer`")
        return ParsedAction(action="final_answer", answer=answer)
    raise ActionParseError(
        "action must be either 'call_tool' or 'final_answer' "
        f"(got: {action!r}; payload={_truncate(payload)})"
    )


def _truncate(value: Any, n: int = 240) -> str:
    raw = repr(value)
    if len(raw) <= n:
        return raw
    return raw[:n] + "...(truncated)"
