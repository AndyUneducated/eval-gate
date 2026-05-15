"""Negative-path coverage: missing set, missing trace, trace without LLM span."""

from __future__ import annotations

from datetime import datetime, timedelta

from httpx import AsyncClient


async def test_get_unknown_eval_set_returns_404(client: AsyncClient) -> None:
    resp = await client.get("/v1/eval-sets/does-not-exist")
    assert resp.status_code == 404


async def test_add_case_to_unknown_set_returns_404(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/eval-sets/nope/cases",
        json={"input": {"q": "?"}, "task_type": "generic"},
    )
    assert resp.status_code == 404


async def test_from_trace_unknown_trace_returns_404(client: AsyncClient) -> None:
    set_id = (await client.post("/v1/eval-sets", json={"name": "x"})).json()["id"]
    resp = await client.post(
        f"/v1/eval-sets/{set_id}/cases/from-trace/missing-trace-id",
        json={},
    )
    assert resp.status_code == 404


async def test_from_trace_without_llm_span_returns_422(client: AsyncClient) -> None:
    base = datetime(2026, 5, 14, 12, 0, 0)
    # A trace with only a non-LLM span — no gen_ai.*, no evalgate.kind=llm.
    await client.post(
        "/v1/traces",
        json={
            "spans": [
                {
                    "trace_id": "noLLM",
                    "span_id": "s1",
                    "name": "http.request",
                    "kind": "other",
                    "start_time": base.isoformat(),
                    "end_time": (base + timedelta(seconds=1)).isoformat(),
                    "attributes": {"http.url": "https://example.com"},
                }
            ]
        },
    )
    set_id = (await client.post("/v1/eval-sets", json={"name": "x"})).json()["id"]
    resp = await client.post(
        f"/v1/eval-sets/{set_id}/cases/from-trace/noLLM",
        json={},
    )
    assert resp.status_code == 422
    assert "no LLM span" in resp.json()["detail"]
