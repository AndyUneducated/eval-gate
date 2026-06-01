"""PointwiseJudge parsing layers."""

from __future__ import annotations

import pytest

from evalgate.judge.pointwise import PointwiseJudge
from evalgate.judge.prompt_spec import JudgeSpec

_SPEC = JudgeSpec(model="ollama/qwen3.5:9b", rubric="rate 0..1 strict json")


def _patch(monkeypatch, response: str) -> None:
    async def fake(**kwargs):
        return response, {}

    monkeypatch.setattr("evalgate.judge.pointwise.acompletion_json", fake)


@pytest.mark.asyncio
async def test_parses_valid_json(monkeypatch):
    _patch(monkeypatch, '{"score": 0.83, "reason": "ok"}')
    leaf = await PointwiseJudge(_SPEC).score("input", "output", mock=False)
    assert leaf.score == pytest.approx(0.83)
    call = leaf.calls[0]
    assert call.reason == "ok"
    assert call.judge_model == _SPEC.model
    assert call.score == pytest.approx(0.83)
    assert call.position is None
    assert call.winner is None


@pytest.mark.asyncio
async def test_regex_fallback_on_text(monkeypatch):
    _patch(monkeypatch, "The score is 0.7 overall")
    leaf = await PointwiseJudge(_SPEC).score("input", "output", mock=False)
    assert leaf.score == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_clamps_above_one(monkeypatch):
    _patch(monkeypatch, '{"score": 1.5, "reason": "x"}')
    leaf = await PointwiseJudge(_SPEC).score("input", "output", mock=False)
    assert leaf.score == 1.0


@pytest.mark.asyncio
async def test_unparseable_returns_zero_and_reason(monkeypatch):
    _patch(monkeypatch, "lol nothing useful here")
    leaf = await PointwiseJudge(_SPEC).score("input", "output", mock=False)
    assert leaf.score == 0.0
    assert "lol" in leaf.calls[0].reason
