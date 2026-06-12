"""Phase 13: /v1/shadow/observe, /v1/shadow/reports, /v1/shadow/rollup."""

from __future__ import annotations

from httpx import AsyncClient


def _payload(case_id: str, *, cand_cost: float) -> dict:
    rec = {
        "case_id": case_id,
        "tags": ["billing"],
        "score": 0.8,
        "cost_usd": 0.0020,
        "latency_ms": 800,
    }
    return {
        "case_id": case_id,
        "tags": ["billing"],
        "primary_prompt_hash": "prim",
        "candidate_prompt_hash": "cand",
        "primary": rec,
        "candidate": {**rec, "cost_usd": cand_cost},
    }


async def test_observe_returns_202(client: AsyncClient) -> None:
    resp = await client.post("/v1/shadow/observe", json=_payload("c0", cand_cost=0.0024))
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["id"]


async def test_reports_aggregate_cost_regression(client: AsyncClient) -> None:
    for i in range(30):
        resp = await client.post("/v1/shadow/observe", json=_payload(f"c{i}", cand_cost=0.0024))
        assert resp.status_code == 202

    report_resp = await client.get("/v1/shadow/reports", params={"candidate_prompt_hash": "cand"})
    assert report_resp.status_code == 200
    body = report_resp.json()
    assert body["n_observations"] == 30
    assert body["passed"] is False
    axes = {a["name"]: a for a in body["report"]["axes"]}
    assert axes["cost"]["passed"] is False
    assert axes["cost"]["significant"] is True


async def test_rollup_persists_and_returns_report(client: AsyncClient) -> None:
    for i in range(30):
        await client.post("/v1/shadow/observe", json=_payload(f"c{i}", cand_cost=0.0024))

    rollup_resp = await client.post("/v1/shadow/rollup", params={"candidate_prompt_hash": "cand"})
    assert rollup_resp.status_code == 200
    body = rollup_resp.json()
    assert body["passed"] is False
    assert body["candidate_prompt_hash"] == "cand"
    assert body["n_observations"] == 30


async def test_reports_empty_window_passes(client: AsyncClient) -> None:
    resp = await client.get("/v1/shadow/reports", params={"candidate_prompt_hash": "nope"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_observations"] == 0
    assert body["passed"] is True
