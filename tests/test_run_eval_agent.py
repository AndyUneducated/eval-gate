from __future__ import annotations

from pathlib import Path

import pytest

from evalgate.core.schemas import TaskKind
from evalgate.eval_set import repository as set_repo
from evalgate.evaluator import runner as eval_runner
from evalgate.judge import persistence as judge_repo

_AGENT_PROMPT = """
name: agent-run-test
candidate:
  model: ollama/qwen2.5:7b
  user_template: "{question}"
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
agent_runtime:
  max_steps: 4
  tool_names:
    - lookup_invoice
    - fetch_policy
    - get_payment_attempts
"""

_GENERIC_PROMPT = """
name: generic-run-test
candidate:
  model: ollama/qwen2.5:7b
  user_template: "{question}"
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
def agent_prompt_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "agent.yaml"
    p.write_text(_AGENT_PROMPT)
    return p


@pytest.fixture
def generic_prompt_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "generic.yaml"
    p.write_text(_GENERIC_PROMPT)
    return p


async def _seed_agent_set(session, n: int = 2):
    s = await set_repo.create_eval_set(session, name="agent-runner-test")
    for i in range(n):
        await set_repo.add_case(
            session,
            set_id=s.id,
            task_type=TaskKind.agent,
            input={"question": f"q{i}"},
            expected={"answer": f"a{i}"},
            expected_trajectory=[
                {"tool": "lookup_invoice", "args": {}},
                {"tool": "fetch_policy", "args": {}},
            ],
            tags=["agent", "billing"],
        )
    return s


@pytest.mark.asyncio
async def test_run_eval_agent_persists_submetrics_and_calls(db_session_factory, agent_prompt_yaml):
    async with db_session_factory() as session:
        s = await _seed_agent_set(session, n=2)

    async with db_session_factory() as session:
        result = await eval_runner.run_eval(
            session,
            eval_set_id_or_name=s.id,
            prompt_path=str(agent_prompt_yaml),
            mock=True,
        )

    assert result.total_cases == 2
    assert len(result.records) == 2
    for rec in result.records:
        assert rec.axis_breakdown is not None
        quality = rec.axis_breakdown["quality"]
        assert set(quality) == {"tool_call_accuracy", "step_wise_success"}
        assert rec.score == pytest.approx(
            (quality["tool_call_accuracy"] + quality["step_wise_success"]) / 2
        )

    async with db_session_factory() as session:
        results = await judge_repo.list_results(session, result.run_id)
    assert len(results) == 2
    assert all(r.axis_breakdown is not None for r in results)
    assert all("quality" in (r.axis_breakdown or {}) for r in results)
    assert all(r.judge_raw is not None and "actual_trajectory" in r.judge_raw for r in results)


@pytest.mark.asyncio
async def test_agent_case_without_agent_runtime_is_unsupported(
    db_session_factory, generic_prompt_yaml
):
    async with db_session_factory() as session:
        s = await _seed_agent_set(session, n=1)

    async with db_session_factory() as session:
        result = await eval_runner.run_eval(
            session,
            eval_set_id_or_name=s.id,
            prompt_path=str(generic_prompt_yaml),
            mock=True,
        )

    assert result.total_cases == 1
    rec = result.records[0]
    assert rec.score == 0.0
    assert rec.error is True
    assert rec.error_kind == "unsupported_task_type"
