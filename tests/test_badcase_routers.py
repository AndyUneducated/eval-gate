"""REST coverage for /v1/badcases endpoints (list + promote, Phase 7.5 model)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from evalgate.db.models import EvalResultRow, EvalRunRow, EvalSetRow
from evalgate.eval_set import repository as set_repo


def _id() -> str:
    return uuid4().hex


async def _seed(factory: async_sessionmaker) -> dict:
    async with factory() as session:
        src = EvalSetRow(id=_id(), name="src")
        dst = EvalSetRow(id=_id(), name="dst")
        session.add_all([src, dst])
        await session.commit()
        src_id, dst_id = src.id, dst.id

    async with factory() as session:
        case = await set_repo.add_case(
            session,
            set_id=src_id,
            task_type="generic",
            input={"prompt": "x"},
            expected={"output": "ref"},
            tags=["billing"],
        )
        case_id = case.id

    async with factory() as session:
        run = EvalRunRow(
            id=_id(),
            eval_set_id=src_id,
            prompt_path="p",
            prompt_hash="h" * 64,
            candidate_model="m",
            judge_model="j",
        )
        session.add(run)
        await session.commit()
        results = []
        for conf in (0.2, 0.9, 0.5):
            r = EvalResultRow(
                id=_id(),
                eval_run_id=run.id,
                eval_case_id=case_id,
                tags=["billing"],
                output={"text": "out"},
                score=0.5,
                cost_usd=0.0,
                latency_ms=10,
                judge_confidence=conf,
            )
            session.add(r)
            results.append((conf, r))
        await session.commit()
        return {
            "src_id": src_id,
            "dst_id": dst_id,
            "case_id": case_id,
            "run_id": run.id,
            "result_ids": [r.id for _, r in results],
            "lowest_conf_result_id": next(r.id for c, r in results if c == 0.2),
        }


@pytest.mark.asyncio
async def test_list_uncertainty_orders_low_confidence_first(
    client: AsyncClient, db_session_factory
):
    seeded = await _seed(db_session_factory)
    resp = await client.get(
        "/v1/badcases",
        params={"strategy": "uncertainty", "run_id": seeded["run_id"], "limit": 10},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy"] == "uncertainty"
    confs = [item["judge_confidence"] for item in body["items"]]
    assert confs == [0.2, 0.5, 0.9]


@pytest.mark.asyncio
async def test_list_unknown_strategy_returns_422(client: AsyncClient):
    resp = await client.get("/v1/badcases", params={"strategy": "bogus"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_promote_endpoint_creates_membership(client: AsyncClient, db_session_factory):
    seeded = await _seed(db_session_factory)
    resp = await client.post(
        f"/v1/badcases/{seeded['lowest_conf_result_id']}/promote",
        json={
            "target_set": "dst",
            "strategy": "uncertainty",
            "extra_tags": ["from-test"],
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["eval_case_id"] == seeded["case_id"]
    assert body["eval_set_id"] == seeded["dst_id"]
    assert body["promoted_from_result_id"] == seeded["lowest_conf_result_id"]
    assert body["strategy"] == "uncertainty"
    assert body["tags"] == ["from-test"]


@pytest.mark.asyncio
async def test_promote_into_origin_set_returns_409(client: AsyncClient, db_session_factory):
    """Phase 4.5: origin set is just another membership -> AlreadyPromoted (409)."""
    seeded = await _seed(db_session_factory)
    resp = await client.post(
        f"/v1/badcases/{seeded['lowest_conf_result_id']}/promote",
        json={"target_set": seeded["src_id"]},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_promote_twice_returns_409(client: AsyncClient, db_session_factory):
    seeded = await _seed(db_session_factory)
    body = {"target_set": "dst", "strategy": "uncertainty"}
    first = await client.post(f"/v1/badcases/{seeded['lowest_conf_result_id']}/promote", json=body)
    assert first.status_code == 201
    again = await client.post(f"/v1/badcases/{seeded['lowest_conf_result_id']}/promote", json=body)
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_promote_missing_result_returns_404(client: AsyncClient, db_session_factory):
    await _seed(db_session_factory)
    resp = await client.post(
        "/v1/badcases/nonexistent/promote",
        json={"target_set": "dst"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_eval_set_detail_includes_promoted_case(client: AsyncClient, db_session_factory):
    """Phase 4 endpoint surfaces membership-promoted cases too."""
    seeded = await _seed(db_session_factory)
    await client.post(
        f"/v1/badcases/{seeded['lowest_conf_result_id']}/promote",
        json={"target_set": "dst"},
    )
    detail = (await client.get(f"/v1/eval-sets/{seeded['dst_id']}")).json()
    assert [c["id"] for c in detail["cases"]] == [seeded["case_id"]]
