"""run_candidate timing + cost-fallback behaviour, plus the thinking-off
helper that lives in ``judge.protocol`` and is applied at every call site
to keep thinking-capable models from burning ``num_predict`` budget on
``<think>`` content (which would silently zero-out our judge scores).

We test the helper directly here (cheap, no I/O) and also assert
``run_candidate`` actually plumbs the helper output to ``litellm.acompletion``
so changes downstream can't regress the contract without this file going
red.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import litellm
import pytest

from evalgate.judge.candidate import run_candidate
from evalgate.judge.prompt_spec import (
    CandidateSpec,
    JudgePolicySpec,
    JudgeSpec,
    PromptSpec,
)
from evalgate.judge.protocol import acompletion_json, thinking_off_kwargs


def _spec(model: str = "ollama/qwen3.5:9b", params: dict | None = None) -> PromptSpec:
    return PromptSpec(
        name="t",
        candidate=CandidateSpec(model=model, user_template="{q}", params=params or {}),
        judges=[JudgeSpec(model=model, rubric="rate")],
        judge_policy=JudgePolicySpec(mode="pointwise", k=1),
    )


@pytest.mark.asyncio
async def test_run_candidate_returns_text_and_latency():
    out = await run_candidate({"q": "hi"}, _spec(), mock_response="hello there")
    assert out.text == "hello there"
    assert out.latency_ms >= 0


@pytest.mark.asyncio
async def test_run_candidate_cost_falls_back_when_pricing_missing(monkeypatch):
    def _explode(**_kwargs):
        raise RuntimeError("no pricing for ollama/*")

    monkeypatch.setattr(litellm, "completion_cost", _explode)
    out = await run_candidate({"q": "hi"}, _spec(), mock_response="ok")
    assert out.cost_usd == 0.0


# ---------------------------------------------------------------------------
# thinking_off_kwargs: provider switch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,expected",
    [
        ("ollama/qwen3.5:9b", {"think": False}),
        ("ollama/qwen3.6:27b", {"think": False}),
        # Embedding model still gets the ollama treatment — harmless, no
        # embedding endpoint reads `think`; LiteLLM drop_params=True saves
        # us if a future version starts forwarding it to /api/embeddings.
        ("ollama/qwen3-embedding:8b", {"think": False}),
        ("anthropic/claude-opus-4-7", {"reasoning_effort": "none"}),
        ("claude-3-7-sonnet-20250219", {"reasoning_effort": "none"}),
        ("openai/o1-mini", {"reasoning_effort": "minimal"}),
        ("openai/o3-mini", {"reasoning_effort": "minimal"}),
        ("openai/gpt-5-thinking", {"reasoning_effort": "minimal"}),
        ("gemini/gemini-2.5-pro", {"reasoning_effort": "none"}),
        ("gemini/gemini-3-flash", {"reasoning_effort": "none"}),
        # Non-thinking providers: helper returns nothing, drop_params handles
        # any residue downstream.
        ("openai/gpt-4o", {}),
        ("openai/gpt-4o-mini", {}),
        ("gemini/gemini-1.5-pro", {}),
        ("unknown-provider/foo", {}),
        ("", {}),
    ],
)
def test_thinking_off_kwargs_provider_switch(model: str, expected: dict[str, Any]) -> None:
    assert thinking_off_kwargs(model) == expected


def test_thinking_off_kwargs_is_case_insensitive_on_model() -> None:
    """Some users uppercase model names; the prefix check must tolerate it."""

    assert thinking_off_kwargs("OLLAMA/Qwen3.5:9B") == {"think": False}


def test_thinking_off_kwargs_returns_fresh_dict() -> None:
    """Caller mutates the returned dict (we merge it in by setdefault).
    Make sure two calls don't share state."""

    a = thinking_off_kwargs("ollama/qwen3.5:9b")
    a["think"] = True  # tamper
    b = thinking_off_kwargs("ollama/qwen3.5:9b")
    assert b == {"think": False}


# ---------------------------------------------------------------------------
# thinking-off integration at the call sites
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_candidate_injects_thinking_off_for_ollama(monkeypatch) -> None:
    """Default path: an Ollama candidate gets `think: False` even though
    the YAML didn't ask for it. This is the whole point of the helper —
    without it Qwen3.x silently returns empty content."""

    captured: dict[str, Any] = {}

    async def _fake(**kwargs: Any):
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(litellm, "acompletion", AsyncMock(side_effect=_fake))

    await run_candidate({"q": "hi"}, _spec(model="ollama/qwen3.5:9b"))
    assert captured.get("think") is False


@pytest.mark.asyncio
async def test_run_candidate_yaml_params_win_over_thinking_off(monkeypatch) -> None:
    """Opt-in path: user wrote `params: {think: true}`; the helper must
    NOT clobber that (setdefault semantics)."""

    captured: dict[str, Any] = {}

    async def _fake(**kwargs: Any):
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(litellm, "acompletion", AsyncMock(side_effect=_fake))

    spec = _spec(model="ollama/qwen3.5:9b", params={"think": True})
    await run_candidate({"q": "hi"}, spec)
    assert captured.get("think") is True


@pytest.mark.asyncio
async def test_run_candidate_skips_thinking_kwargs_for_unknown_provider(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake(**kwargs: Any):
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(litellm, "acompletion", AsyncMock(side_effect=_fake))

    await run_candidate({"q": "hi"}, _spec(model="unknown-provider/foo"))
    assert "think" not in captured
    assert "reasoning_effort" not in captured


@pytest.mark.asyncio
async def test_acompletion_json_injects_thinking_off_for_anthropic(monkeypatch) -> None:
    """Same setdefault contract holds for the judge-side acompletion_json."""

    captured: dict[str, Any] = {}

    async def _fake(**kwargs: Any):
        captured.update(kwargs)
        return {"choices": [{"message": {"content": '{"score": 0.5}'}}]}

    monkeypatch.setattr(litellm, "acompletion", AsyncMock(side_effect=_fake))

    await acompletion_json(
        model="anthropic/claude-opus-4-7",
        messages=[{"role": "user", "content": "x"}],
    )
    assert captured.get("reasoning_effort") == "none"


@pytest.mark.asyncio
async def test_acompletion_json_yaml_params_win_over_thinking_off(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake(**kwargs: Any):
        captured.update(kwargs)
        return {"choices": [{"message": {"content": '{"score": 0.5}'}}]}

    monkeypatch.setattr(litellm, "acompletion", AsyncMock(side_effect=_fake))

    await acompletion_json(
        model="anthropic/claude-opus-4-7",
        messages=[{"role": "user", "content": "x"}],
        params={"reasoning_effort": "high"},
    )
    assert captured.get("reasoning_effort") == "high"
