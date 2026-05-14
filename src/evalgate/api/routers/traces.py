"""POST /v1/traces — OTel/OTLP-flavored span ingest.

This commit only validates + parses; persistence to Postgres is wired in the
next commit alongside the BadCase finder.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from evalgate.core.logging import get_logger
from evalgate.core.schemas import Span
from evalgate.ingest.otel_mapper import map_otel_span

log = get_logger("evalgate.api.traces")
router = APIRouter()


class TraceIngestRequest(BaseModel):
    spans: list[dict[str, Any]]


class TraceIngestResponse(BaseModel):
    accepted: int
    trace_ids: list[str]


@router.post(
    "/traces",
    response_model=TraceIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_traces(payload: TraceIngestRequest) -> TraceIngestResponse:
    if not payload.spans:
        raise HTTPException(status_code=400, detail="spans list is empty")

    parsed: list[Span] = []
    for raw in payload.spans:
        try:
            parsed.append(map_otel_span(raw))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    trace_ids = sorted({span.trace_id for span in parsed})
    log.info("traces.ingest", count=len(parsed), trace_ids=trace_ids)
    # TODO(persistence): write spans via async SQLAlchemy session.
    return TraceIngestResponse(accepted=len(parsed), trace_ids=trace_ids)
