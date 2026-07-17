"""Trace ingest + browse endpoints.

* ``POST /v1/traces``           — simplified JSON ingest (SDK-less callers, tests).
* ``GET  /v1/traces``           — paginated list, ordered by start_time DESC.
* ``GET  /v1/traces/{trace_id}`` — single trace + all its spans (start_time ASC).

The OTLP/HTTP wire-format ingest lives in `evalgate.api.routers.otlp`; both
endpoints share the persistence layer (`evalgate.ingest.persistence`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from evalgate.api.deps import SessionDep
from evalgate.core.logging import get_logger
from evalgate.core.schemas import Span
from evalgate.ingest import persistence
from evalgate.ingest.otel_mapper import map_otel_span

log = get_logger("evalgate.api.traces")
router = APIRouter()


class TraceIngestRequest(BaseModel):
    spans: list[dict[str, Any]]
    resource_attributes: dict[str, Any] = Field(default_factory=dict)


class TraceIngestResponse(BaseModel):
    accepted: int
    trace_ids: list[str]


class TraceListItem(BaseModel):
    trace_id: str
    service_name: str | None
    start_time: datetime
    end_time: datetime
    span_count: int


class TraceListResponse(BaseModel):
    traces: list[TraceListItem]


class SpanItem(BaseModel):
    span_id: str
    trace_id: str
    parent_span_id: str | None
    name: str
    kind: str
    start_time: datetime
    end_time: datetime
    attributes: dict[str, Any]
    status_code: str
    status_message: str | None


class TraceDetail(BaseModel):
    trace_id: str
    service_name: str | None
    start_time: datetime
    end_time: datetime
    span_count: int
    resource_attributes: dict[str, Any]
    spans: list[SpanItem]


@router.post(
    "/traces",
    response_model=TraceIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def ingest_traces(
    payload: TraceIngestRequest,
    session: SessionDep,
) -> TraceIngestResponse:
    if not payload.spans:
        raise HTTPException(status_code=400, detail="spans list is empty")

    parsed: list[Span] = []
    for raw in payload.spans:
        try:
            parsed.append(map_otel_span(raw))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    trace_ids = await persistence.persist_spans(session, parsed, payload.resource_attributes)
    log.info("traces.ingest", count=len(parsed), trace_ids=trace_ids)
    return TraceIngestResponse(accepted=len(parsed), trace_ids=trace_ids)


@router.get("/traces", response_model=TraceListResponse)
async def list_traces(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    since: Annotated[
        datetime | None, Query(description="ISO-8601 lower bound on start_time")
    ] = None,
    service: Annotated[str | None, Query(description="filter by service.name")] = None,
) -> TraceListResponse:
    rows = await persistence.list_traces(session, limit=limit, since=since, service=service)
    return TraceListResponse(
        traces=[
            TraceListItem(
                trace_id=r.trace_id,
                service_name=r.service_name,
                start_time=r.start_time,
                end_time=r.end_time,
                span_count=r.span_count,
            )
            for r in rows
        ]
    )


@router.get("/traces/{trace_id}", response_model=TraceDetail)
async def get_trace(
    trace_id: str,
    session: SessionDep,
) -> TraceDetail:
    trace, spans = await persistence.get_trace(session, trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"trace {trace_id!r} not found")
    return TraceDetail(
        trace_id=trace.trace_id,
        service_name=trace.service_name,
        start_time=trace.start_time,
        end_time=trace.end_time,
        span_count=trace.span_count,
        resource_attributes=trace.resource_attributes,
        spans=[
            SpanItem(
                span_id=s.span_id,
                trace_id=s.trace_id,
                parent_span_id=s.parent_span_id,
                name=s.name,
                kind=s.kind,
                start_time=s.start_time,
                end_time=s.end_time,
                attributes=s.attributes,
                status_code=s.status_code,
                status_message=s.status_message,
            )
            for s in spans
        ],
    )
