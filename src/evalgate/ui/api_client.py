"""Thin synchronous HTTP wrapper around the EvalGate `/v1/*` REST surface.

Streamlit's execution model re-runs page scripts top-to-bottom on every
interaction, which makes long-lived async clients painful. We use a sync
``httpx.Client`` and a dedicated client object so all URL/parameter logic
lives in one place — the page modules just call methods.

Errors surface as `EvalGateAPIError` carrying the HTTP status + parsed
body so streamlit can render them with `st.error()` without leaking raw
tracebacks to the browser.

Tests live in ``tests/test_ui_api_client.py`` and use
``httpx.MockTransport`` so we never need to spin up uvicorn.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from evalgate.core.schemas import EvalCaseOut, EvalSetDetail, EvalSetOut, GateReport

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 15.0


class EvalGateAPIError(RuntimeError):
    """Non-2xx response from the EvalGate API.

    Attributes:
        status_code: HTTP status the server returned.
        detail: parsed `detail` field if the body was JSON-shaped, else raw text.
    """

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(f"EvalGate API returned {status_code}: {detail!r}")
        self.status_code = status_code
        self.detail = detail


class EvalGateClient:
    """Synchronous client used by streamlit pages.

    Construct via the env var ``EVALGATE_API_URL`` or pass ``base_url``
    explicitly. The underlying ``httpx.Client`` may be injected for tests
    via ``transport=httpx.MockTransport(...)``.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("EVALGATE_API_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> EvalGateClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, **params: Any) -> Any:
        clean = {k: v for k, v in params.items() if v is not None}
        resp = self._client.get(path, params=clean)
        return self._unwrap(resp)

    def _post(self, path: str, *, json: Any) -> Any:
        resp = self._client.post(path, json=json)
        return self._unwrap(resp)

    @staticmethod
    def _unwrap(resp: httpx.Response) -> Any:
        if resp.status_code >= 400:
            try:
                body = resp.json()
                detail = body.get("detail", body) if isinstance(body, dict) else body
            except ValueError:
                detail = resp.text
            raise EvalGateAPIError(resp.status_code, detail)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    def healthz(self) -> dict[str, Any]:
        return self._get("/healthz")

    def list_traces(
        self,
        *,
        limit: int = 50,
        service: str | None = None,
        since: str | None = None,
    ) -> list[dict[str, Any]]:
        body = self._get("/v1/traces", limit=limit, service=service, since=since)
        return list(body.get("traces", []))

    def get_trace(self, trace_id: str) -> dict[str, Any]:
        return self._get(f"/v1/traces/{trace_id}")

    def list_eval_sets(self, *, limit: int = 50) -> list[EvalSetOut]:
        body = self._get("/v1/eval-sets", limit=limit)
        return [EvalSetOut.model_validate(s) for s in body.get("eval_sets", [])]

    def get_eval_set(self, set_id: str) -> EvalSetDetail:
        body = self._get(f"/v1/eval-sets/{set_id}")
        return EvalSetDetail.model_validate(body)

    def create_eval_set(self, *, name: str, description: str | None = None) -> EvalSetOut:
        body = self._post(
            "/v1/eval-sets",
            json={"name": name, "description": description},
        )
        return EvalSetOut.model_validate(body)

    def add_case_from_trace(
        self,
        *,
        set_id: str,
        trace_id: str,
        tags: list[str] | None = None,
    ) -> EvalCaseOut:
        body = self._post(
            f"/v1/eval-sets/{set_id}/cases/from-trace/{trace_id}",
            json={"tags": tags or []},
        )
        return EvalCaseOut.model_validate(body)

    def list_runs(
        self,
        *,
        eval_set_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        body = self._get("/v1/runs", eval_set_id=eval_set_id, limit=limit)
        return list(body.get("runs", []))

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/v1/runs/{run_id}")

    def get_run_records(self, run_id: str) -> list[dict[str, Any]]:
        body = self._get(f"/v1/runs/{run_id}/records")
        return list(body.get("records", []))

    def run_gate(
        self,
        *,
        baseline: list[dict[str, Any]],
        candidate: list[dict[str, Any]],
    ) -> GateReport:
        body = self._post(
            "/v1/evals/run",
            json={"baseline": baseline, "candidate": candidate},
        )
        return GateReport.model_validate(body)

    def seed_demo_trace(self, spec: dict[str, Any]) -> list[str]:
        """POST a `TraceSpec`-shaped dict to the dev seed-trace endpoint.

        Returns the resulting ``trace_ids``. The server validates the spec
        with pydantic, so any structural error comes back as 422 ->
        ``EvalGateAPIError``.
        """
        body = self._post("/v1/dev/seed-trace", json=spec)
        return list(body.get("trace_ids", []))
