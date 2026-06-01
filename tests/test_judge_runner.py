"""Runner end-to-end on aiosqlite.

We seed an eval_set with 3 cases, run the runner in mock mode, and assert:
- 3 result rows land in the DB,
- the returned `records` carry every field gate's multi-axis extractors read,
- `EvalRunRow` is finalised with total_cases + mean_score.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evalgate.eval_set import repository as set_repo
from evalgate.evaluator import runner
from evalgate.judge import persistence as judge_repo

_PROMPT = """
name: t
candidate:
  model: ollama/qwen3.5:9b
  system: "be careful"
  user_template: "Q: {prompt}"
  params: {}
judges:
  - model: ollama/qwen3.5:9b
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


async def _seed_set(session, n: int = 3):
    s = await set_repo.create_eval_set(session, name="judge-runner-test")
    for i in range(n):
        await set_repo.add_case(
            session,
            set_id=s.id,
            input={"prompt": f"q{i}"},
            expected={"answer": f"a{i}"},
            tags=["billing"],
        )
    return s


@pytest.mark.asyncio
async def test_run_eval_persists_records_and_finalises_run(db_session_factory, prompt_yaml):
    async with db_session_factory() as session:
        s = await _seed_set(session, n=3)

    async with db_session_factory() as session:
        result = await runner.run_eval(
            session,
            eval_set_id_or_name=s.id,
            prompt_path=str(prompt_yaml),
            mock=True,
        )

    assert result.total_cases == 3
    assert len(result.records) == 3
    assert all(r.score == pytest.approx(0.5) for r in result.records)
    assert result.mean_score == pytest.approx(0.5)

    # Records carry the contract fields gate needs.
    for rec in result.records:
        d = rec.model_dump()
        for key in ("case_id", "tags", "score", "cost_usd", "latency_ms"):
            assert key in d

    # Persistence side-effects: 3 result rows + finalised run row.
    async with db_session_factory() as session:
        results = await judge_repo.list_results(session, result.run_id)
        run = await judge_repo.get_run(session, result.run_id)
    assert len(results) == 3
    assert run is not None
    assert run.total_cases == 3
    assert run.mean_score == pytest.approx(0.5)
    assert run.candidate_model == "ollama/qwen3.5:9b"


@pytest.mark.asyncio
async def test_run_eval_resolves_set_by_name(db_session_factory, prompt_yaml):
    async with db_session_factory() as session:
        await _seed_set(session, n=2)

    async with db_session_factory() as session:
        result = await runner.run_eval(
            session,
            eval_set_id_or_name="judge-runner-test",
            prompt_path=str(prompt_yaml),
            mock=True,
        )
    assert result.total_cases == 2


@pytest.mark.asyncio
async def test_run_eval_unknown_set_raises(db_session_factory, prompt_yaml):
    async with db_session_factory() as session:
        with pytest.raises(set_repo.EvalSetNotFoundError):
            await runner.run_eval(
                session,
                eval_set_id_or_name="ghost",
                prompt_path=str(prompt_yaml),
                mock=True,
            )
