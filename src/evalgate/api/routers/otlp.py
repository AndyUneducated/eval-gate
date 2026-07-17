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

from fastapi import APIRouter, HTTPException, Request, Response, status
from google.protobuf.json_format import ParseError
from google.protobuf.message import DecodeError

from evalgate.api.deps import SessionDep
from evalgate.core.config import get_settings
from evalgate.core.logging import get_logger
from evalgate.ingest.otlp import parse_otlp_json, parse_otlp_protobuf
from evalgate.ingest.persistence import persist_spans

log = get_logger("evalgate.api.otlp")
router = APIRouter()


@router.post("/otel/traces")
async def ingest_otlp(request: Request, session: SessionDep) -> Response:
    ctype = request.headers.get("content-type", "").lower()
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty body")
    # Hard cap independent of Content-Length: the middleware guard only sees the
    # declared length, so a chunked / header-less upload would otherwise slip a
    # multi-GB protobuf straight into memory here (the actual DoS vector).
    max_bytes = get_settings().max_request_bytes
    if len(body) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"request body exceeds {max_bytes} bytes",
        )

    try:
        if "protobuf" in ctype:
            spans, resource_attrs = parse_otlp_protobuf(body)
            response_ctype = "application/x-protobuf"
        else:
            payload = json.loads(body)
            spans, resource_attrs = parse_otlp_json(payload)
            response_ctype = "application/json"
    except (ValueError, json.JSONDecodeError, DecodeError, ParseError) as exc:
        # protobuf's DecodeError / json_format.ParseError are NOT ValueError
        # subclasses; without them a malformed body escapes as a 500.
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
