"""REST coverage for the eval-set CRUD endpoints (sets + manual case add)."""

from __future__ import annotations

from httpx import AsyncClient


async def test_create_eval_set_returns_id(client: AsyncClient) -> None:
    resp = await client.post("/v1/eval-sets", json={"name": "billing-regress"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "billing-regress"
    assert body["id"]
    assert body["created_at"]


async def test_list_eval_sets_returns_all_created(client: AsyncClient) -> None:
    # NB: SQLite's NOW() is second-resolution, so we can't assert ordering of
    # rows inserted in the same second. We only assert membership. Production
    # Postgres has microsecond resolution and orders correctly.
    for name in ("first", "second", "third"):
        r = await client.post("/v1/eval-sets", json={"name": name})
        assert r.status_code == 201

    listing = (await client.get("/v1/eval-sets")).json()["eval_sets"]
    assert {s["name"] for s in listing} == {"first", "second", "third"}


async def test_add_case_explicit_payload(client: AsyncClient) -> None:
    set_id = (await client.post("/v1/eval-sets", json={"name": "manual"})).json()["id"]
    payload = {
        "task_type": "rag",
        "input": {"question": "what is RAG?"},
        "expected": {"answer": "retrieval-augmented generation"},
        "tags": ["docs"],
    }
    resp = await client.post(f"/v1/eval-sets/{set_id}/cases", json=payload)
    assert resp.status_code == 201
    case = resp.json()
    assert case["task_type"] == "rag"
    assert case["input"]["question"] == "what is RAG?"
    assert case["tags"] == ["docs"]

    detail = (await client.get(f"/v1/eval-sets/{set_id}")).json()
    assert len(detail["cases"]) == 1
    assert detail["cases"][0]["id"] == case["id"]


async def test_get_eval_set_by_name(client: AsyncClient) -> None:
    await client.post("/v1/eval-sets", json={"name": "by-name", "description": "demo"})
    detail = await client.get("/v1/eval-sets/by-name")
    assert detail.status_code == 200
    body = detail.json()
    assert body["name"] == "by-name"
    assert body["description"] == "demo"
    assert body["cases"] == []
