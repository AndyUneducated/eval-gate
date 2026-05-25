"""PointwiseJudge parsing layers — same contract as the deleted RubricJudge:

- well-formed JSON -> exact score + reason
- garbage text containing `score: 0.7` -> regex tolerance kicks in
- nothing parseable -> score=0, reason=raw (we never raise)
- numeric overflow -> clamped to [0, 1]
"""

from __future__ import annotations

import pytest

from evalgate.judge.pointwise import PointwiseJudge
from evalgate.judge.prompt_spec import JudgeSpec

_SPEC = JudgeSpec(model="ollama/qwen3.5:9b", rubric="rate 0..1 strict json")


@pytest.mark.asyncio
async def test_parses_valid_json():
    judge = PointwiseJudge(_SPEC)
    verdict, call = await judge.score(
        "input", "output", mock_response='{"score": 0.83, "reason": "ok"}'
    )
    assert verdict.score == pytest.approx(0.83)
    assert verdict.reason == "ok"
    assert call.judge_model == _SPEC.model
    assert call.score == pytest.approx(0.83)
    assert call.position is None
    assert call.winner is None


@pytest.mark.asyncio
async def test_regex_fallback_on_text():
    judge = PointwiseJudge(_SPEC)
    verdict, _ = await judge.score("input", "output", mock_response="The score is 0.7 overall")
    assert verdict.score == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_clamps_above_one():
    judge = PointwiseJudge(_SPEC)
    verdict, _ = await judge.score("input", "output", mock_response='{"score": 1.5, "reason": "x"}')
    assert verdict.score == 1.0


@pytest.mark.asyncio
async def test_unparseable_returns_zero_and_reason():
    judge = PointwiseJudge(_SPEC)
    verdict, _ = await judge.score("input", "output", mock_response="lol nothing useful here")
    assert verdict.score == 0.0
    assert "lol" in verdict.reason
