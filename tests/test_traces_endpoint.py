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

    # Verify persistence — the same trace must be visible via the browse API.
    detail = await client.get("/v1/traces/t1")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["span_count"] == 1
    assert detail_body["spans"][0]["name"] == "chat"


async def test_ingest_traces_rejects_empty(client: AsyncClient) -> None:
    resp = await client.post("/v1/traces", json={"spans": []})
    assert resp.status_code == 400


async def test_ingest_traces_rejects_missing_ids(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/traces",
        json={"spans": [{"name": "broken"}]},
    )
    assert resp.status_code == 422


async def test_run_evals_returns_three_scalar_axes_without_safety_breakdown(client: AsyncClient) -> None:
    records = [
        {"case_id": "c1", "tags": ["qa"], "score": 0.9, "cost_usd": 0.01, "latency_ms": 1000},
        {"case_id": "c2", "tags": ["qa"], "score": 0.85, "cost_usd": 0.011, "latency_ms": 1100},
    ]
    resp = await client.post("/v1/evals/run", json={"baseline": records, "candidate": records})
    assert resp.status_code == 200
    body = resp.json()
    assert body["passed"] is True
    assert {axis["name"] for axis in body["axes"]} == {
        "quality",
        "cost",
        "latency_p95",
    }
