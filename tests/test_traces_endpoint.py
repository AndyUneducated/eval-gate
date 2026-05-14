from __future__ import annotations

from httpx import AsyncClient


async def test_ingest_traces_accepts_simple_span(client: AsyncClient) -> None:
    payload = {
        "spans": [
            {
                "span_id": "s1",
                "trace_id": "t1",
                "name": "chat",
                "kind": "llm",
                "start_time": "2026-05-14T00:00:00+00:00",
                "end_time": "2026-05-14T00:00:01+00:00",
                "attributes": {"gen_ai.system": "openai"},
            }
        ]
    }
    resp = await client.post("/v1/traces", json=payload)
    assert resp.status_code == 202
    body = resp.json()
    assert body == {"accepted": 1, "trace_ids": ["t1"]}


async def test_ingest_traces_rejects_empty(client: AsyncClient) -> None:
    resp = await client.post("/v1/traces", json={"spans": []})
    assert resp.status_code == 400


async def test_ingest_traces_rejects_missing_ids(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/traces",
        json={"spans": [{"name": "broken"}]},
    )
    assert resp.status_code == 422


async def test_run_evals_returns_skeleton_report(client: AsyncClient) -> None:
    resp = await client.post("/v1/evals/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["passed"] is True
    assert {axis["name"] for axis in body["axes"]} == {
        "quality",
        "cost",
        "latency_p95",
        "safety",
    }
