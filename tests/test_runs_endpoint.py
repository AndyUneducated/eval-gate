"""REST coverage for /v1/runs and /v1/runs/{id}.

Phase 11: the Streamlit Reports page picks two runs from this endpoint
and feeds their records into the existing `/v1/evals/run` gate. Tests
exercise the list filter / limit + 404 path.
"""

from __future__ import annotations

from httpx import AsyncClient

from evalgate.eval_set import repository as set_repo
from evalgate.judge import persistence


async def _seed_run(session, *, eval_set_id: str, prompt_path: str = "p.yaml") -> str:
    run = await persistence.create_run(
        session,
        eval_set_id=eval_set_id,
        prompt_path=prompt_path,
        prompt_hash="deadbeef",
        candidate_model="ollama/qwen3.5:9b",
        judge_model="ollama/qwen3.5:9b",
    )
    return run.id


async def test_list_runs_empty_returns_empty_array(client: AsyncClient) -> None:
    resp = await client.get("/v1/runs")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


async def test_list_runs_filters_by_eval_set(client: AsyncClient, db_session_factory) -> None:
    async with db_session_factory() as session:
        s1 = await set_repo.create_eval_set(session, name="set-a")
        s2 = await set_repo.create_eval_set(session, name="set-b")
        run_a1 = await _seed_run(session, eval_set_id=s1.id)
        run_a2 = await _seed_run(session, eval_set_id=s1.id, prompt_path="p2.yaml")
        run_b1 = await _seed_run(session, eval_set_id=s2.id)

    listing = (await client.get(f"/v1/runs?eval_set_id={s1.id}")).json()["runs"]
    assert {r["id"] for r in listing} == {run_a1, run_a2}
    assert all(r["eval_set_id"] == s1.id for r in listing)

    listing_b = (await client.get(f"/v1/runs?eval_set_id={s2.id}")).json()["runs"]
    assert {r["id"] for r in listing_b} == {run_b1}

    all_runs = (await client.get("/v1/runs")).json()["runs"]
    assert {r["id"] for r in all_runs} == {run_a1, run_a2, run_b1}


async def test_list_runs_respects_limit(client: AsyncClient, db_session_factory) -> None:
    async with db_session_factory() as session:
        s = await set_repo.create_eval_set(session, name="lots")
        for _ in range(5):
            await _seed_run(session, eval_set_id=s.id)

    body = (await client.get("/v1/runs?limit=3")).json()
    assert len(body["runs"]) == 3


async def test_get_run_404_when_missing(client: AsyncClient) -> None:
    resp = await client.get("/v1/runs/does-not-exist")
    assert resp.status_code == 404


async def test_get_run_returns_meta(client: AsyncClient, db_session_factory) -> None:
    async with db_session_factory() as session:
        s = await set_repo.create_eval_set(session, name="single")
        run_id = await _seed_run(session, eval_set_id=s.id, prompt_path="prompt.yaml")

    body = (await client.get(f"/v1/runs/{run_id}")).json()
    assert body["id"] == run_id
    assert body["eval_set_id"] == s.id
    assert body["prompt_path"] == "prompt.yaml"
    assert body["candidate_model"] == "ollama/qwen3.5:9b"
    assert body["total_cases"] == 0
