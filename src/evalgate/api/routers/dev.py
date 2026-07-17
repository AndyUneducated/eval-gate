"""Developer-only routes for local demos and ops UI conveniences.

Mounted under ``/v1/dev`` so callers can clearly tell these endpoints apart
from the production data path. They are **not** intended for production
clients and may change without semver guarantees.

Currently exposes:

* ``POST /v1/dev/seed-trace`` — accept a :class:`TraceSpec`, build an
  OTLP-JSON envelope, feed it through the same parser + persistence layer
  real OTLP exports use, and return the resulting ``trace_ids``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from evalgate.api.deps import SessionDep
from evalgate.core.logging import get_logger
from evalgate.dev.trace_seeder import TraceSpec, build_otlp_envelope
from evalgate.ingest.otlp import parse_otlp_json
from evalgate.ingest.persistence import persist_spans

log = get_logger("evalgate.api.dev")
router = APIRouter()


class SeedTraceResponse(BaseModel):
    trace_ids: list[str] = Field(default_factory=list)
    span_count: int = 0


@router.post(
    "/dev/seed-trace",
    response_model=SeedTraceResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def seed_trace(spec: TraceSpec, session: SessionDep) -> SeedTraceResponse:
    """Generate one or more demo traces and persist them via the OTLP path.

    The request body validates against :class:`TraceSpec` (count bounds,
    required service_name, etc.); structural issues come back as 422 from
    FastAPI's pydantic layer. We surface envelope-construction problems as
    400 so the UI can show a useful message.
    """
    try:
        envelope: dict[str, Any] = build_otlp_envelope(spec)
        spans, resource_attrs = parse_otlp_json(envelope)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    trace_ids = await persist_spans(session, spans, resource_attrs)
    log.info(
        "dev.seed_trace",
        trace_count=len(trace_ids),
        span_count=len(spans),
        service=spec.service_name,
        count=spec.count,
    )
    return SeedTraceResponse(trace_ids=trace_ids, span_count=len(spans))
