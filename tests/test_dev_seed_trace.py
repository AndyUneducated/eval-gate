"""Integration tests for ``POST /v1/dev/seed-trace``.

Reuses the in-memory aiosqlite ``client`` fixture so we exercise the full
FastAPI stack: pydantic validation -> seeder -> OTLP parser -> persistence
-> trace list/detail readback.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


def _rag_spec(**overrides):
    spec = {
        "service_name": "demo-app",
        "tracer_name": "evalgate-ui-demo",
        "count": 1,
        "root": {
            "name": "rag-pipeline",
            "kind": "chain",
            "attributes": {"evalgate.tag": "billing"},
        },
        "retriever": {
            "name": "retriever.search",
            "kind": "retriever",
            "attributes": {"retriever.k": 3},
        },
        "llm": {
            "name": "llm.call",
            "kind": "llm",
            "attributes": {},
            "gen_ai_system": "openai",
            "gen_ai_model": "gpt-4o-mini",
            "prompt": "hi",
            "use_mock_response": True,
            "mock_response": "hello",
        },
        "extra_resource_attributes": {},
    }
    spec.update(overrides)
    return spec


@pytest.mark.asyncio
async def test_seed_trace_accepts_rag_template(client: AsyncClient) -> None:
    resp = await client.post("/v1/dev/seed-trace", json=_rag_spec())
    assert resp.status_code == 202
    body = resp.json()
    assert len(body["trace_ids"]) == 1
    assert body["span_count"] == 3

    trace_id = body["trace_ids"][0]
    detail = await client.get(f"/v1/traces/{trace_id}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["service_name"] == "demo-app"
    assert detail_body["span_count"] == 3

    names = sorted(s["name"] for s in detail_body["spans"])
    assert names == ["llm.call", "rag-pipeline", "retriever.search"]


@pytest.mark.asyncio
async def test_seed_trace_count_greater_than_one(client: AsyncClient) -> None:
    resp = await client.post("/v1/dev/seed-trace", json=_rag_spec(count=3))
    assert resp.status_code == 202
    body = resp.json()
    assert len(body["trace_ids"]) == 3
    # 3 traces * 3 spans (chain + retriever + llm)
    assert body["span_count"] == 9

    listing = await client.get("/v1/traces", params={"service": "demo-app", "limit": 50})
    assert listing.status_code == 200
    listing_body = listing.json()
    listed_ids = {t["trace_id"] for t in listing_body["traces"]}
    for tid in body["trace_ids"]:
        assert tid in listed_ids


@pytest.mark.asyncio
async def test_seed_trace_rejects_count_above_max(client: AsyncClient) -> None:
    resp = await client.post("/v1/dev/seed-trace", json=_rag_spec(count=999))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_seed_trace_minimum_spec_only_root(client: AsyncClient) -> None:
    spec = {
        "service_name": "tiny-app",
        "root": {"name": "root-only", "kind": "chain", "attributes": {}},
    }
    resp = await client.post("/v1/dev/seed-trace", json=spec)
    assert resp.status_code == 202
    body = resp.json()
    assert body["span_count"] == 1


@pytest.mark.asyncio
async def test_seed_trace_extra_resource_attributes_persist(client: AsyncClient) -> None:
    spec = _rag_spec()
    spec["extra_resource_attributes"] = {"deployment.environment": "staging"}
    resp = await client.post("/v1/dev/seed-trace", json=spec)
    assert resp.status_code == 202

    trace_id = resp.json()["trace_ids"][0]
    detail = await client.get(f"/v1/traces/{trace_id}")
    assert detail.json()["resource_attributes"].get("deployment.environment") == "staging"


@pytest.mark.asyncio
async def test_seed_trace_rejects_empty_service_name(client: AsyncClient) -> None:
    spec = _rag_spec()
    spec["service_name"] = ""
    resp = await client.post("/v1/dev/seed-trace", json=spec)
    assert resp.status_code == 422
