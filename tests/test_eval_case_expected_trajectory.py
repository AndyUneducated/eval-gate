from __future__ import annotations

import pytest

from evalgate.core.schemas import TaskKind
from evalgate.eval_set import repository as set_repo


@pytest.mark.asyncio
async def test_add_case_persists_expected_trajectory(db_session_factory):
    trajectory = [
        {"tool": "lookup_invoice", "args": {"invoice_id": "INV-42"}},
        {"tool": "fetch_policy", "args": {"topic": "billing"}},
    ]
    async with db_session_factory() as session:
        s = await set_repo.create_eval_set(session, name="agent-cases")
        row = await set_repo.add_case(
            session,
            set_id=s.id,
            task_type=TaskKind.agent,
            input={"question": "q"},
            expected={"answer": "a"},
            expected_trajectory=trajectory,
        )
    assert list(row.expected_trajectory) == trajectory


@pytest.mark.asyncio
async def test_add_case_default_expected_trajectory_is_empty_list(db_session_factory):
    async with db_session_factory() as session:
        s = await set_repo.create_eval_set(session, name="generic-cases-trajectory")
        row = await set_repo.add_case(
            session,
            set_id=s.id,
            input={"prompt": "ping"},
        )
    assert list(row.expected_trajectory) == []


@pytest.mark.asyncio
async def test_create_case_via_rest_with_expected_trajectory(client):
    set_resp = await client.post("/v1/eval-sets", json={"name": "agent-rest"})
    set_id = set_resp.json()["id"]
    payload = {
        "task_type": "agent",
        "input": {"question": "q"},
        "expected": {"answer": "a"},
        "expected_trajectory": [
            {"tool": "lookup_invoice", "args": {"invoice_id": "INV-42"}},
            {"tool": "fetch_policy", "args": {"topic": "billing"}},
        ],
    }
    resp = await client.post(f"/v1/eval-sets/{set_id}/cases", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["expected_trajectory"] == payload["expected_trajectory"]

    detail = await client.get(f"/v1/eval-sets/{set_id}")
    cases = detail.json()["cases"]
    assert cases[0]["expected_trajectory"] == payload["expected_trajectory"]
