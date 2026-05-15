"""OTLP/HTTP trace ingest endpoint.

The official OTel Python SDK's `OTLPSpanExporter` POSTs protobuf bodies to
``/v1/traces``; we mount this on ``/v1/otel/traces`` to keep the simpler
JSON ingest at ``/v1/traces`` free for SDK-less callers and tests. The demo
app wires the exporter to this exact path with the `endpoint=` kwarg.

The endpoint returns an empty `ExportTraceServiceResponse` (an empty body in
the same content-type as the request) which is what every OTLP/HTTP client
expects on success.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.core.logging import get_logger
from evalgate.db.session import get_session
from evalgate.ingest.otlp import parse_otlp_json, parse_otlp_protobuf
from evalgate.ingest.persistence import persist_spans

log = get_logger("evalgate.api.otlp")
router = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/otel/traces")
async def ingest_otlp(request: Request, session: SessionDep) -> Response:
    ctype = request.headers.get("content-type", "").lower()
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty body")

    try:
        if "protobuf" in ctype:
            spans, resource_attrs = parse_otlp_protobuf(body)
            response_ctype = "application/x-protobuf"
        else:
            payload = json.loads(body)
            spans, resource_attrs = parse_otlp_json(payload)
            response_ctype = "application/json"
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"unparseable OTLP payload: {exc}") from exc

    trace_ids = await persist_spans(session, spans, resource_attrs)
    log.info(
        "otlp.ingest",
        content_type=ctype,
        span_count=len(spans),
        trace_count=len(trace_ids),
    )

    # Empty ExportTraceServiceResponse: spec-compliant successful body.
    empty = b"{}" if response_ctype == "application/json" else b""
    return Response(content=empty, media_type=response_ctype, status_code=status.HTTP_200_OK)
