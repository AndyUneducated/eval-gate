"""Offline tests for `evalgate.ui.api_client.EvalGateClient`.

We use ``httpx.MockTransport`` so we never need to spin up uvicorn or hit
the database. The goal is to lock the URL / params / parse contract that
the streamlit pages depend on — actual server behaviour is exercised by
the FastAPI integration tests next door.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from evalgate.ui.api_client import DEFAULT_BASE_URL, EvalGateAPIError, EvalGateClient


def _make_client(handler) -> EvalGateClient:
    transport = httpx.MockTransport(handler)
    return EvalGateClient(base_url="http://test.local", transport=transport)


def test_default_base_url_falls_back_to_localhost(monkeypatch) -> None:
    monkeypatch.delenv("EVALGATE_API_URL", raising=False)
    client = EvalGateClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})))
    assert client.base_url == DEFAULT_BASE_URL.rstrip("/")
    client.close()


def test_env_overrides_base_url(monkeypatch) -> None:
    monkeypatch.setenv("EVALGATE_API_URL", "http://api.example.com:9000/")
    client = EvalGateClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})))
    assert client.base_url == "http://api.example.com:9000"
    client.close()


def test_list_traces_sends_clean_params() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"traces": [{"trace_id": "t1"}]})

    with _make_client(handler) as client:
        traces = client.list_traces(limit=20, service=None, since=None)

    assert traces == [{"trace_id": "t1"}]
    assert seen["url"].startswith("http://test.local/v1/traces")
    # `None` params must be stripped, only `limit` should make it on the wire.
    assert seen["params"] == {"limit": "20"}


def test_list_eval_sets_parses_pydantic() -> None:
    payload = {
        "eval_sets": [
            {
                "id": "abc",
                "name": "demo",
                "description": None,
                "created_at": "2025-01-01T00:00:00Z",
                "updated_at": "2025-01-01T00:00:00Z",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/eval-sets"
        return httpx.Response(200, json=payload)

    with _make_client(handler) as client:
        sets = client.list_eval_sets()

    assert len(sets) == 1
    assert sets[0].id == "abc"
    assert sets[0].name == "demo"


def test_list_runs_filters_eval_set_id() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"runs": []})

    with _make_client(handler) as client:
        client.list_runs(eval_set_id="set-1", limit=7)

    assert seen["params"] == {"eval_set_id": "set-1", "limit": "7"}


def test_get_run_records_returns_list() -> None:
    payload = {
        "run_id": "r1",
        "records": [
            {
                "case_id": "c1",
                "tags": ["billing"],
                "score": 0.9,
                "cost_usd": 0.0,
                "latency_ms": 100,
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/runs/r1/records"
        return httpx.Response(200, json=payload)

    with _make_client(handler) as client:
        records = client.get_run_records("r1")

    assert records == payload["records"]


def test_run_gate_posts_baseline_and_candidate() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "passed": True,
                "axes": [],
                "attribution": {},
                "summary": "ok",
            },
        )

    with _make_client(handler) as client:
        report = client.run_gate(
            baseline=[{"case_id": "c1", "score": 0.9}],
            candidate=[{"case_id": "c1", "score": 0.4}],
        )

    assert seen["path"] == "/v1/evals/run"
    assert seen["body"]["baseline"][0]["case_id"] == "c1"
    assert seen["body"]["candidate"][0]["score"] == 0.4
    assert report.passed is True
    assert report.summary == "ok"


def test_non_2xx_raises_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "trace 't' not found"})

    with _make_client(handler) as client, pytest.raises(EvalGateAPIError) as exc:
        client.get_trace("t")

    assert exc.value.status_code == 404
    assert exc.value.detail == "trace 't' not found"


def test_non_json_error_body_falls_back_to_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal boom")

    with _make_client(handler) as client, pytest.raises(EvalGateAPIError) as exc:
        client.healthz()

    assert exc.value.status_code == 500
    assert "internal boom" in str(exc.value.detail)
