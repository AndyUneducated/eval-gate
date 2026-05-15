"""run_candidate timing + cost-fallback behaviour.

`completion_cost` raises for models with no published pricing (the typical
case for local Ollama). The wrapper must treat that as $0 rather than crash.
"""

from __future__ import annotations

import litellm
import pytest

from evalgate.judge.candidate import run_candidate
from evalgate.judge.prompt_spec import (
    CandidateSpec,
    JudgePolicySpec,
    JudgeSpec,
    PromptSpec,
)


def _spec(model: str = "ollama/qwen2.5:7b") -> PromptSpec:
    return PromptSpec(
        name="t",
        candidate=CandidateSpec(model=model, user_template="{q}"),
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
