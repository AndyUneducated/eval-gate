"""Builtin tool runtime for Phase 9 agent evaluator.

Tools are intentionally small deterministic mocks:
- enough to validate trajectory correctness end-to-end,
- cheap and offline-friendly for CI (`--mock`),
- zero external service coupling.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol


class Tool(Protocol):
    name: str

    async def run(self, args: dict[str, Any]) -> Any: ...


@dataclass
class FnTool:
    name: str
    fn: Callable[[dict[str, Any]], Awaitable[Any]]

    async def run(self, args: dict[str, Any]) -> Any:
        return await self.fn(args)


class ToolNotFoundError(LookupError):
    pass


class BuiltinToolRegistry:
    def __init__(self, tools: dict[str, Tool]):
        self._tools = tools

    @classmethod
    def from_names(cls, tool_names: list[str]) -> BuiltinToolRegistry:
        catalog = _builtin_catalog()
        missing = [name for name in tool_names if name not in catalog]
        if missing:
            raise ToolNotFoundError(f"unknown tools in prompt spec: {missing!r}")
        return cls({name: catalog[name] for name in tool_names})

    async def run(self, tool_name: str, args: dict[str, Any]) -> Any:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ToolNotFoundError(f"tool not allowed by prompt spec: {tool_name!r}")
        return await tool.run(args)

    @property
    def names(self) -> list[str]:
        return list(self._tools.keys())


def _builtin_catalog() -> dict[str, Tool]:
    async def lookup_invoice(args: dict[str, Any]) -> dict[str, Any]:
        invoice_id = str(args.get("invoice_id") or "INV-000")
        return {
            "invoice_id": invoice_id,
            "status": "open",
            "amount_due_usd": 42.0,
            "due_days": 14,
        }

    async def get_payment_attempts(args: dict[str, Any]) -> dict[str, Any]:
        invoice_id = str(args.get("invoice_id") or "INV-000")
        return {
            "invoice_id": invoice_id,
            "attempts": [
                {"at": "2026-05-01T10:00:00Z", "status": "failed"},
                {"at": "2026-05-02T10:00:00Z", "status": "failed"},
            ],
        }

    async def fetch_policy(args: dict[str, Any]) -> dict[str, Any]:
        topic = str(args.get("topic") or "billing")
        return {
            "topic": topic,
            "policy": "Late payments incur a 1.5% monthly finance charge.",
        }

    return {
        "lookup_invoice": FnTool("lookup_invoice", lookup_invoice),
        "get_payment_attempts": FnTool("get_payment_attempts", get_payment_attempts),
        "fetch_policy": FnTool("fetch_policy", fetch_policy),
    }
