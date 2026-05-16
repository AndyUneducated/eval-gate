"""End-to-end: two runner invocations -> records JSON -> `evalgate gate`.

This is the Phase 5 exit-criterion test: prove the runner's output schema is
*directly* consumable by Phase 2's gate, with no glue code. We control the
two runs' scores by monkeypatching `litellm.acompletion` so the judge sees
different mock responses on each call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from evalgate.eval_set import repository as set_repo
from evalgate.evaluator import runner
from evalgate.gate.decision import build_gate_report

_PROMPT = """
name: t
candidate:
  model: ollama/qwen2.5:7b
  user_template: "{prompt}"
  params: {}
judges:
  - model: ollama/qwen2.5:7b
    rubric: "rate 0..1 strict json"
    params: {}
judge_policy:
  mode: pointwise
  k: 1
  position_swap: false
  concurrency: 2
"""


@pytest.fixture
def prompt_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "p.yaml"
    p.write_text(_PROMPT)
    return p


def _fake_completion_factory(judge_score: float):
    """Return an async fake of litellm.acompletion that emits a fixed text for
    the candidate role and a `{"score": ...}` JSON for the judge role. We
    distinguish them by looking at whether the rubric keyword appears in the
    user message — simple but stable for this test's prompt.yaml.
    """

    class _Resp(dict):
        pass

    def _resp(text: str) -> dict:
        return _Resp(choices=[{"message": {"content": text}}])

    async def _fake(**kwargs: Any) -> dict:
        msgs = kwargs.get("messages") or []
        last = msgs[-1]["content"] if msgs else ""
        if "rate" in last.lower():  # judge call
            return _resp(f'{{"score": {judge_score}, "reason": "fake"}}')
        return _resp("fake candidate output")

    return _fake


async def _seed(session, n: int = 4):
    s = await set_repo.create_eval_set(session, name="e2e")
    for i in range(n):
        await set_repo.add_case(
            session,
            set_id=s.id,
            input={"prompt": f"q{i}"},
            tags=["billing"],
        )
    return s


@pytest.mark.asyncio
async def test_runner_records_feed_gate(db_session_factory, prompt_yaml, monkeypatch):
    import litellm

    async with db_session_factory() as session:
        await _seed(session, n=4)

    # Run 1: baseline (judge always returns 0.9).
    monkeypatch.setattr(litellm, "acompletion", _fake_completion_factory(0.9))
    async with db_session_factory() as session:
        baseline = await runner.run_eval(
            session,
            eval_set_id_or_name="e2e",
            prompt_path=str(prompt_yaml),
            mock=False,
        )

    # Run 2: candidate (judge regresses to 0.4).
    monkeypatch.setattr(litellm, "acompletion", _fake_completion_factory(0.4))
    async with db_session_factory() as session:
        candidate = await runner.run_eval(
            session,
            eval_set_id_or_name="e2e",
            prompt_path=str(prompt_yaml),
            mock=False,
        )

    assert baseline.mean_score == pytest.approx(0.9)
    assert candidate.mean_score == pytest.approx(0.4)

    report = build_gate_report(
        [r.model_dump() for r in baseline.records],
        [r.model_dump() for r in candidate.records],
    )

    # All four axes present, named exactly as Phase 2 expects.
    assert {a.name for a in report.axes} == {
        "quality",
        "cost",
        "latency_p95",
        "safety",
    }
    quality = next(a for a in report.axes if a.name == "quality")
    assert quality.baseline == pytest.approx(0.9)
    assert quality.candidate == pytest.approx(0.4)
    assert quality.delta < 0
    assert isinstance(report.passed, bool)
