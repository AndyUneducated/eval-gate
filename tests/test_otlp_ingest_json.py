"""OTLP-JSON envelope ingest. Same convergence as the protobuf path, but
sometimes more convenient for curl debugging and integration tests."""

from __future__ import annotations

from httpx import AsyncClient


def _envelope() -> dict:
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "demo-app"}},
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "11111111111111111111111111111111",
                                "spanId": "aaaaaaaaaaaaaaaa",
                                "name": "rag-pipeline",
                                "kind": 1,
                                "startTimeUnixNano": "1700000000000000000",
                                "endTimeUnixNano": "1700000002000000000",
                                "attributes": [
                                    {
                                        "key": "evalgate.kind",
                                        "value": {"stringValue": "chain"},
                                    }
                                ],
                            },
                            {
                                "traceId": "11111111111111111111111111111111",
                                "spanId": "bbbbbbbbbbbbbbbb",
                                "parentSpanId": "aaaaaaaaaaaaaaaa",
                                "name": "llm.call",
                                "kind": 3,
                                "startTimeUnixNano": "1700000000100000000",
                                "endTimeUnixNano": "1700000001500000000",
                                "attributes": [
                                    {
                                        "key": "gen_ai.system",
                                        "value": {"stringValue": "openai"},
                                    },
                                    {
                                        "key": "evalgate.kind",
                                        "value": {"stringValue": "llm"},
                                    },
                                ],
                            },
                        ]
                    }
                ],
            }
        ]
    }


async def test_otlp_json_ingest_persists_trace(client: AsyncClient) -> None:
    resp = await client.post("/v1/otel/traces", json=_envelope())
    assert resp.status_code == 200

    listing = (await client.get("/v1/traces")).json()
    assert len(listing["traces"]) == 1
    assert listing["traces"][0]["service_name"] == "demo-app"
    assert listing["traces"][0]["span_count"] == 2


async def test_otlp_json_ingest_rejects_garbage(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/otel/traces",
        content=b"not-json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422
