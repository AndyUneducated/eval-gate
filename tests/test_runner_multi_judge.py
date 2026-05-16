"""End-to-end runner with the full MultiJudge stack on aiosqlite.

Verifies:
- 2 judges x K=2 x P=1 (pointwise) -> 4 eval_judge_calls per case
- pairwise mode skips cases missing `expected` with `error=True`
- judge_confidence is populated on EvalRecord and EvalResultRow
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evalgate.eval_set import repository as set_repo
from evalgate.evaluator import runner
from evalgate.judge import persistence as judge_repo

_POINTWISE_YAML = """
name: t
candidate:
  model: ollama/qwen2.5:7b
  user_template: "Q: {prompt}"
  params: {}
judges:
  - model: ollama/qwen2.5:7b
    rubric: "rate"
    params: {}
  - model: ollama/qwen2.5:32b
    rubric: "rate"
    params: {}
judge_policy:
  mode: pointwise
  k: 2
  position_swap: false
  concurrency: 4
"""

_PAIRWISE_YAML = """
name: t
candidate:
  model: ollama/qwen2.5:7b
  user_template: "Q: {prompt}"
  params: {}
judges:
  - model: ollama/qwen2.5:7b
    rubric: "compare"
    params: {}
judge_policy:
  mode: pairwise
  k: 1
  position_swap: true
  concurrency: 4
"""


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "p.yaml"
    p.write_text(body)
    return p


async def _seed(session, n: int, *, with_expected: bool):
    s = await set_repo.create_eval_set(session, name="rmj")
    for i in range(n):
        await set_repo.add_case(
            session,
            set_id=s.id,
            input={"prompt": f"q{i}"},
            expected={"output": f"ref{i}"} if with_expected else None,
            tags=["billing"],
        )
    return s


@pytest.mark.asyncio
async def test_pointwise_two_judges_k2_writes_four_calls_per_case(db_session_factory, tmp_path):
    yaml = _write(tmp_path, _POINTWISE_YAML)
    async with db_session_factory() as session:
        s = await _seed(session, n=2, with_expected=False)

    async with db_session_factory() as session:
        result = await runner.run_eval(
            session,
            eval_set_id_or_name=s.id,
            prompt_path=str(yaml),
            mock=True,
        )

    assert result.total_cases == 2
    assert all(r.score == pytest.approx(0.5) for r in result.records)
    assert all(getattr(r, "judge_confidence", None) is not None for r in result.records)

    async with db_session_factory() as session:
        rows = await judge_repo.list_results(session, result.run_id)
        for row in rows:
            calls = await judge_repo.list_judge_calls(session, row.id)
            # 2 judges x K=2 = 4 raw calls per result
            assert len(calls) == 4
            assert {c.judge_model for c in calls} == {
                "ollama/qwen2.5:7b",
                "ollama/qwen2.5:32b",
            }


@pytest.mark.asyncio
async def test_pairwise_mode_with_expected_emits_swap_calls(db_session_factory, tmp_path):
    yaml = _write(tmp_path, _PAIRWISE_YAML)
    async with db_session_factory() as session:
        s = await _seed(session, n=1, with_expected=True)

    async with db_session_factory() as session:
        result = await runner.run_eval(
            session,
            eval_set_id_or_name=s.id,
            prompt_path=str(yaml),
            mock=True,
        )

    assert result.total_cases == 1
    async with db_session_factory() as session:
        rows = await judge_repo.list_results(session, result.run_id)
        calls = await judge_repo.list_judge_calls(session, rows[0].id)
    # 1 judge x K=1 x P=2 (swap) = 2 calls
    assert len(calls) == 2
    assert {c.position for c in calls} == {"A_FIRST", "B_FIRST"}


@pytest.mark.asyncio
async def test_pairwise_missing_expected_emits_error_record(db_session_factory, tmp_path):
    yaml = _write(tmp_path, _PAIRWISE_YAML)
    async with db_session_factory() as session:
        s = await _seed(session, n=2, with_expected=False)

    async with db_session_factory() as session:
        result = await runner.run_eval(
            session,
            eval_set_id_or_name=s.id,
            prompt_path=str(yaml),
            mock=True,
        )

    assert result.total_cases == 2
    for rec in result.records:
        d = rec.model_dump()
        assert d.get("error") is True
        assert d.get("error_kind") == "missing_reference"
        assert d["score"] == 0.0

    # No judge calls should have been issued because we skipped before judging.
    async with db_session_factory() as session:
        rows = await judge_repo.list_results(session, result.run_id)
        for row in rows:
            assert await judge_repo.list_judge_calls(session, row.id) == []
