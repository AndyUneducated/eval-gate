"""End-to-end coverage of the trace browse API:

* `POST /v1/traces` simplified ingest writes spans + a trace rollup.
* `GET  /v1/traces` paginates by `start_time DESC`, supports `since` + `service`.
* `GET  /v1/traces/{trace_id}` returns the full span tree, sorted by start_time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient


def _span(trace_id: str, span_id: str, start: datetime, **extra):
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "name": extra.pop("name", "op"),
        "kind": extra.pop("kind", "llm"),
        "start_time": start.isoformat(),
        "end_time": (start + timedelta(milliseconds=500)).isoformat(),
        "attributes": extra.pop("attributes", {}),
        **extra,
    }


async def test_post_traces_persists_and_lists(client: AsyncClient) -> None:
    base = datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)
    payloads = [
        {
            "resource_attributes": {"service.name": "svc-a"},
            "spans": [
                _span("t1", "s1", base, name="root"),
                _span("t1", "s2", base + timedelta(seconds=1), name="child", parent_span_id="s1"),
            ],
        },
        {
            "resource_attributes": {"service.name": "svc-b"},
            "spans": [_span("t2", "s3", base + timedelta(seconds=10))],
        },
        {
            "resource_attributes": {"service.name": "svc-a"},
            "spans": [_span("t3", "s4", base + timedelta(seconds=20))],
        },
    ]
    for p in payloads:
        r = await client.post("/v1/traces", json=p)
        assert r.status_code == 202, r.text

    listing = (await client.get("/v1/traces?limit=10")).json()["traces"]
    assert [t["trace_id"] for t in listing] == ["t3", "t2", "t1"]
    assert listing[2]["span_count"] == 2

    by_service = (await client.get("/v1/traces?service=svc-a")).json()["traces"]
    assert {t["trace_id"] for t in by_service} == {"t1", "t3"}

    cutoff = (base + timedelta(seconds=5)).isoformat()
    after = (await client.get("/v1/traces", params={"since": cutoff})).json()["traces"]
    assert {t["trace_id"] for t in after} == {"t2", "t3"}


async def test_get_trace_detail_returns_spans_in_order(client: AsyncClient) -> None:
    base = datetime(2026, 5, 14, 12, 0, 0, tzinfo=UTC)
    spans = [
        _span("tX", "b", base + timedelta(seconds=2), name="middle", parent_span_id="a"),
        _span("tX", "a", base, name="root"),
        _span("tX", "c", base + timedelta(seconds=4), name="last", parent_span_id="a"),
    ]
    await client.post("/v1/traces", json={"spans": spans})

    detail = (await client.get("/v1/traces/tX")).json()
    assert [s["span_id"] for s in detail["spans"]] == ["a", "b", "c"]
    assert detail["span_count"] == 3


async def test_get_trace_detail_404(client: AsyncClient) -> None:
    resp = await client.get("/v1/traces/missing")
    assert resp.status_code == 404
