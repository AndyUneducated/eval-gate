"""Verify `POST /v1/otel/traces` accepts an OTLP/HTTP protobuf payload and
persists both spans + the rolled-up trace row."""

from __future__ import annotations

import time

from httpx import AsyncClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import (
    ResourceSpans,
    ScopeSpans,
)
from opentelemetry.proto.trace.v1.trace_pb2 import (
    Span as PbSpan,
)


def _build_request() -> bytes:
    now_ns = int(time.time() * 1e9)
    root = PbSpan(
        trace_id=b"\x11" * 16,
        span_id=b"\xaa" * 8,
        name="rag-pipeline",
        kind=PbSpan.SPAN_KIND_INTERNAL,
        start_time_unix_nano=now_ns,
        end_time_unix_nano=now_ns + 2_000_000_000,
        attributes=[
            KeyValue(key="evalgate.kind", value=AnyValue(string_value="chain")),
        ],
    )
    child = PbSpan(
        trace_id=b"\x11" * 16,
        span_id=b"\xbb" * 8,
        parent_span_id=b"\xaa" * 8,
        name="llm.call",
        kind=PbSpan.SPAN_KIND_CLIENT,
        start_time_unix_nano=now_ns + 100_000_000,
        end_time_unix_nano=now_ns + 1_500_000_000,
        attributes=[
            KeyValue(key="gen_ai.system", value=AnyValue(string_value="openai")),
            KeyValue(key="evalgate.kind", value=AnyValue(string_value="llm")),
        ],
    )
    req = ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                resource=Resource(
                    attributes=[
                        KeyValue(key="service.name", value=AnyValue(string_value="demo-app")),
                    ]
                ),
                scope_spans=[ScopeSpans(spans=[root, child])],
            )
        ]
    )
    return req.SerializeToString()


async def test_otlp_protobuf_ingest_persists_trace(client: AsyncClient) -> None:
    body = _build_request()
    resp = await client.post(
        "/v1/otel/traces",
        content=body,
        headers={"content-type": "application/x-protobuf"},
    )
    assert resp.status_code == 200

    # Verify via the read-side: trace + spans are queryable.
    listing = await client.get("/v1/traces")
    assert listing.status_code == 200
    traces = listing.json()["traces"]
    assert len(traces) == 1
    assert traces[0]["span_count"] == 2
    assert traces[0]["service_name"] == "demo-app"

    trace_id = traces[0]["trace_id"]
    detail = await client.get(f"/v1/traces/{trace_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert len(body["spans"]) == 2
    assert body["resource_attributes"]["service.name"] == "demo-app"
    names = {s["name"] for s in body["spans"]}
    assert names == {"rag-pipeline", "llm.call"}


async def test_otlp_protobuf_ingest_is_idempotent(client: AsyncClient) -> None:
    body = _build_request()
    for _ in range(2):
        resp = await client.post(
            "/v1/otel/traces",
            content=body,
            headers={"content-type": "application/x-protobuf"},
        )
        assert resp.status_code == 200

    listing = (await client.get("/v1/traces")).json()
    assert len(listing["traces"]) == 1
    # span_count must not double-count on replay.
    assert listing["traces"][0]["span_count"] == 2
