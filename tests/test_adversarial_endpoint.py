"""Adversarial REST API: generate -> pending -> review -> stats (+ error paths).

All generation runs with ``?mock=1`` so the endpoints are deterministic and
offline.
"""

from __future__ import annotations

from httpx import AsyncClient


async def _create_set(client: AsyncClient, name: str = "billing") -> str:
    resp = await client.post("/v1/eval-sets", json={"name": name})
    assert resp.status_code == 201
    return resp.json()["id"]


async def _add_case(client: AsyncClient, set_id: str, *, tag: str = "billing") -> str:
    resp = await client.post(
        f"/v1/eval-sets/{set_id}/cases",
        json={"input": {"question": "base?"}, "expected": {"answer": "a"}, "tags": [tag]},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_generate_pending_review_stats_flow(client: AsyncClient):
    set_id = await _create_set(client)
    await _add_case(client, set_id)

    # generate
    gen = await client.post(
        f"/v1/eval-sets/{set_id}/adversarial", params={"tag": "billing", "k": 5, "mock": True}
    )
    assert gen.status_code == 201
    body = gen.json()
    assert body["tag"] == "billing"
    assert body["requested"] == 5
    assert len(body["created"]) == 5
    for c in body["created"]:
        assert c["status"] == "pending"
        assert c["source"] == "adversarial"

    # pending list
    pend = await client.get(f"/v1/eval-sets/{set_id}/adversarial/pending")
    assert pend.status_code == 200
    pending = pend.json()
    assert len(pending) == 5

    # approve first, reject second
    approve_id = pending[0]["id"]
    reject_id = pending[1]["id"]
    r1 = await client.post(f"/v1/adversarial/{approve_id}/review", json={"decision": "approve"})
    assert r1.status_code == 200
    assert r1.json()["status"] == "active"
    r2 = await client.post(f"/v1/adversarial/{reject_id}/review", json={"decision": "reject"})
    assert r2.status_code == 200
    assert r2.json()["status"] == "archived"

    # pending now down to 3
    pend2 = await client.get(f"/v1/eval-sets/{set_id}/adversarial/pending")
    assert len(pend2.json()) == 3

    # stats: 1 approved active adversarial case, none evaluated yet
    stats = await client.get(f"/v1/eval-sets/{set_id}/adversarial/stats")
    assert stats.status_code == 200
    s = stats.json()
    assert s["total"] == 1
    assert s["evaluated"] == 0
    assert s["hit_rate"] == 0.0
    assert s["threshold"] == 0.5


async def test_generate_unknown_set_404(client: AsyncClient):
    resp = await client.post(
        "/v1/eval-sets/ghost/adversarial", params={"tag": "billing", "k": 2, "mock": True}
    )
    assert resp.status_code == 404


async def test_review_unknown_case_404(client: AsyncClient):
    resp = await client.post("/v1/adversarial/nope/review", json={"decision": "approve"})
    assert resp.status_code == 404


async def test_review_bad_decision_422(client: AsyncClient):
    set_id = await _create_set(client, name="bad-decision")
    await _add_case(client, set_id)
    gen = await client.post(
        f"/v1/eval-sets/{set_id}/adversarial", params={"tag": "billing", "k": 1, "mock": True}
    )
    case_id = gen.json()["created"][0]["id"]
    resp = await client.post(f"/v1/adversarial/{case_id}/review", json={"decision": "maybe"})
    assert resp.status_code == 422


async def test_generate_requires_tag_422(client: AsyncClient):
    set_id = await _create_set(client, name="no-tag")
    resp = await client.post(f"/v1/eval-sets/{set_id}/adversarial", params={"k": 2, "mock": True})
    assert resp.status_code == 422


async def test_stats_threshold_out_of_range_422(client: AsyncClient):
    set_id = await _create_set(client, name="thr")
    resp = await client.get(f"/v1/eval-sets/{set_id}/adversarial/stats", params={"threshold": 1.5})
    assert resp.status_code == 422
