"""Phase 8: ``eval_cases.retrieved_contexts`` round-trips through ORM and API.

We don't run Alembic in tests (conftest builds schema via metadata, see
the comment there), so this exercises the column at the model + repo +
HTTP-payload level rather than the migration directly.
"""

from __future__ import annotations

import pytest

from evalgate.core.schemas import TaskKind
from evalgate.eval_set import repository as set_repo


@pytest.mark.asyncio
async def test_add_case_persists_retrieved_contexts(db_session_factory):
    contexts = [
        "Acme bills monthly. Invoices are due 14 days later.",
        "Refunds appear within 5-10 business days.",
    ]
    async with db_session_factory() as session:
        s = await set_repo.create_eval_set(session, name="rag-cases")
        row = await set_repo.add_case(
            session,
            set_id=s.id,
            task_type=TaskKind.rag,
            input={"question": "when due?"},
            expected={"answer": "14 days"},
            retrieved_contexts=contexts,
        )

    assert list(row.retrieved_contexts) == contexts


@pytest.mark.asyncio
async def test_add_case_default_retrieved_contexts_is_empty_list(db_session_factory):
    async with db_session_factory() as session:
        s = await set_repo.create_eval_set(session, name="generic-cases")
        row = await set_repo.add_case(
            session,
            set_id=s.id,
            input={"prompt": "ping"},
        )
    assert list(row.retrieved_contexts) == []


@pytest.mark.asyncio
async def test_create_case_via_rest_with_retrieved_contexts(client):
    set_resp = await client.post("/v1/eval-sets", json={"name": "rag-rest"})
    set_id = set_resp.json()["id"]
    payload = {
        "task_type": "rag",
        "input": {"question": "when due?"},
        "expected": {"answer": "14 days"},
        "retrieved_contexts": ["chunk-A", "chunk-B"],
    }
    resp = await client.post(f"/v1/eval-sets/{set_id}/cases", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["retrieved_contexts"] == ["chunk-A", "chunk-B"]

    detail = await client.get(f"/v1/eval-sets/{set_id}")
    cases = detail.json()["cases"]
    assert cases[0]["retrieved_contexts"] == ["chunk-A", "chunk-B"]
