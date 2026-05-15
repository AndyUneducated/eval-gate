"""End-to-end coverage of `POST /v1/eval-sets/{id}/cases/from-trace/{trace_id}`.

We seed real data via the Phase 3 simple-JSON ingest endpoint so this test
exercises trace -> span lookup -> case extraction -> case persistence.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from httpx import AsyncClient


def _span(span_id: str, start: datetime, **extra) -> dict:
    return {
        "trace_id": "tdemo",
        "span_id": span_id,
        "name": extra.pop("name", "op"),
        "kind": extra.pop("kind", "other"),
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(milliseconds=200)).isoformat(),
        "attributes": extra.pop("attributes", {}),
        **extra,
    }


async def _seed_trace(client: AsyncClient) -> str:
    base = datetime(2026, 5, 14, 12, 0, 0)
    payload = {
        "resource_attributes": {"service.name": "demo-app"},
        "spans": [
            _span(
                "root",
                base,
                name="rag-pipeline",
                attributes={"evalgate.kind": "chain", "evalgate.tag": "billing"},
            ),
            _span(
                "retr",
                base + timedelta(milliseconds=10),
                name="retriever.search",
                attributes={"evalgate.kind": "retriever"},
                parent_span_id="root",
            ),
            _span(
                "llm",
                base + timedelta(milliseconds=20),
                name="llm.call",
                attributes={
                    "evalgate.kind": "llm",
                    "gen_ai.system": "openai",
                    "gen_ai.prompt": "what is 2+2?",
                    "gen_ai.response.content": "four",
                },
                parent_span_id="root",
            ),
        ],
    }
    r = await client.post("/v1/traces", json=payload)
    assert r.status_code == 202, r.text
    return "tdemo"


async def test_from_trace_promotes_first_llm_span(client: AsyncClient) -> None:
    trace_id = await _seed_trace(client)
    set_id = (await client.post("/v1/eval-sets", json={"name": "promo"})).json()["id"]

    resp = await client.post(
        f"/v1/eval-sets/{set_id}/cases/from-trace/{trace_id}",
        json={"tags": ["smoke"]},
    )
    assert resp.status_code == 201, resp.text
    case = resp.json()

    # First LLM span attributes carried into the case.
    assert case["input"] == {"prompt": "what is 2+2?"}
    assert case["expected"] == {"answer": "four"}
    # Retriever sibling -> task_type=rag.
    assert case["task_type"] == "rag"
    # Root span had evalgate.tag=billing; explicit --tag smoke appended.
    assert case["tags"] == ["billing", "smoke"]
    assert case["source_trace_id"] == trace_id
    assert case["source_span_id"] == "llm"


async def test_from_trace_supports_set_name_lookup(client: AsyncClient) -> None:
    trace_id = await _seed_trace(client)
    await client.post("/v1/eval-sets", json={"name": "byname"})

    resp = await client.post(
        f"/v1/eval-sets/byname/cases/from-trace/{trace_id}",
        json={},
    )
    assert resp.status_code == 201
    detail = (await client.get("/v1/eval-sets/byname")).json()
    assert len(detail["cases"]) == 1


async def test_promote_five_cases_meets_exit_criterion(client: AsyncClient) -> None:
    """Roadmap Phase 4 exit criterion: promote 5 cases into an eval set."""
    # 5 distinct traces, each with one LLM span.
    base = datetime(2026, 5, 14, 12, 0, 0)
    for i in range(5):
        spans = [
            {
                "trace_id": f"t{i}",
                "span_id": f"llm{i}",
                "name": "llm.call",
                "kind": "other",
                "start_time": (base + timedelta(seconds=i)).isoformat(),
                "end_time": (base + timedelta(seconds=i, milliseconds=200)).isoformat(),
                "attributes": {
                    "gen_ai.system": "openai",
                    "gen_ai.prompt": f"q{i}",
                    "gen_ai.response.content": f"a{i}",
                },
            }
        ]
        r = await client.post("/v1/traces", json={"spans": spans})
        assert r.status_code == 202

    set_id = (await client.post("/v1/eval-sets", json={"name": "demo"})).json()["id"]
    for i in range(5):
        r = await client.post(
            f"/v1/eval-sets/{set_id}/cases/from-trace/t{i}",
            json={},
        )
        assert r.status_code == 201, r.text

    detail = (await client.get(f"/v1/eval-sets/{set_id}")).json()
    assert len(detail["cases"]) == 5
    assert all(c["input"] for c in detail["cases"])
    assert {c["source_trace_id"] for c in detail["cases"]} == {f"t{i}" for i in range(5)}
