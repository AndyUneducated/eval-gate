"""OTLP/HTTP body parsers for `POST /v1/otel/traces`.

Protobuf and OTLP-JSON both parse into ``ExportTraceServiceRequest``, then
share one code path into internal ``Span`` models via ``map_otel_span``.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from google.protobuf.json_format import Parse
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span as PbSpan

from evalgate.core.schemas import Span
from evalgate.ingest.otel_mapper import map_otel_span

# OTLP SpanKind enum -> our internal snake_case hint (map_otel_span reads it).
_OTLP_SPAN_KIND: dict[int, str] = {
    0: "other",  # UNSPECIFIED
    1: "other",  # INTERNAL
    2: "server",
    3: "client",
    4: "producer",
    5: "consumer",
}

# OTLP/JSON encodes trace_id / span_id as **hex** strings (a documented
# exception to the protobuf-JSON default of base64). ``json_format.Parse``
# only understands base64 for ``bytes`` fields, so a hex id is silently
# mis-decoded (a 32-char hex trace id becomes 24 bytes). We convert the id
# fields hex -> base64 up front, keyed off the exact hex length, so real
# OTLP/JSON exporters round-trip correctly.
_ID_HEX_LEN: dict[str, int] = {
    "traceId": 32,
    "trace_id": 32,
    "spanId": 16,
    "span_id": 16,
    "parentSpanId": 16,
    "parent_span_id": 16,
}


def _anyvalue_to_python(v: AnyValue) -> Any:
    which = v.WhichOneof("value")
    if which is None:
        return None
    if which == "string_value":
        return v.string_value
    if which == "bool_value":
        return v.bool_value
    if which == "int_value":
        return v.int_value
    if which == "double_value":
        return v.double_value
    if which == "array_value":
        return [_anyvalue_to_python(item) for item in v.array_value.values]
    if which == "kvlist_value":
        return {kv.key: _anyvalue_to_python(kv.value) for kv in v.kvlist_value.values}
    if which == "bytes_value":
        return v.bytes_value.hex()
    return None


def _kv_list_to_dict(kvs: list[KeyValue]) -> dict[str, Any]:
    return {kv.key: _anyvalue_to_python(kv.value) for kv in kvs}


def _pb_span_to_raw(pb: PbSpan) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "span_id": pb.span_id.hex(),
        "trace_id": pb.trace_id.hex(),
        "name": pb.name or "unnamed",
        "start_time_unix_nano": pb.start_time_unix_nano,
        "end_time_unix_nano": pb.end_time_unix_nano,
        "attributes": _kv_list_to_dict(list(pb.attributes)),
    }
    # An empty *or* all-zero parent id means "no parent" (some SDKs emit the
    # 8-zero-byte sentinel); either way this span is a trace root.
    if pb.parent_span_id and pb.parent_span_id != b"\x00" * 8:
        raw["parent_span_id"] = pb.parent_span_id.hex()
    # OTLP StatusCode: 0=UNSET, 1=OK, 2=ERROR. Preserve the UNSET/OK distinction
    # rather than collapsing both to "OK".
    raw["kind"] = _OTLP_SPAN_KIND.get(pb.kind, "other")
    if pb.HasField("status"):
        code_name = {0: "UNSET", 1: "OK", 2: "ERROR"}.get(pb.status.code, "UNSET")
        raw["status"] = {
            "code": code_name,
            "message": pb.status.message or None,
        }
    return raw


def _parse_request(req: ExportTraceServiceRequest) -> tuple[list[Span], dict[str, Any]]:
    spans: list[Span] = []
    resource_attrs: dict[str, Any] = {}
    for rs in req.resource_spans:
        resource_attrs.update(_kv_list_to_dict(list(rs.resource.attributes)))
        for ss in rs.scope_spans:
            for pb_span in ss.spans:
                spans.append(map_otel_span(_pb_span_to_raw(pb_span)))
    return spans, resource_attrs


def parse_otlp_protobuf(body: bytes) -> tuple[list[Span], dict[str, Any]]:
    req = ExportTraceServiceRequest()
    req.ParseFromString(body)
    return _parse_request(req)


def _hex_to_b64(value: Any, expected_hex_len: int) -> Any:
    """Convert an OTLP/JSON hex id to base64 for protobuf-JSON parsing.

    Only rewrites strings that are valid hex of the exact expected length (32
    for trace ids, 16 for span ids); anything else (already-base64, wrong
    length) is returned unchanged so non-conformant producers still work.
    """
    if not isinstance(value, str) or len(value) != expected_hex_len:
        return value
    try:
        raw = bytes.fromhex(value)
    except ValueError:
        return value
    return base64.b64encode(raw).decode("ascii")


def _normalize_json_ids(payload: dict[str, Any]) -> dict[str, Any]:
    """Rewrite hex trace/span ids to base64 in an OTLP-JSON envelope (copy)."""
    import copy

    out = copy.deepcopy(payload)
    for rs in out.get("resourceSpans", []) or []:
        for ss in (rs.get("scopeSpans") or []) + (rs.get("instrumentationLibrarySpans") or []):
            for span in ss.get("spans", []) or []:
                for field, hex_len in _ID_HEX_LEN.items():
                    if field in span:
                        span[field] = _hex_to_b64(span[field], hex_len)
                for link in span.get("links", []) or []:
                    for field, hex_len in _ID_HEX_LEN.items():
                        if field in link:
                            link[field] = _hex_to_b64(link[field], hex_len)
    return out


def parse_otlp_json(payload: dict[str, Any]) -> tuple[list[Span], dict[str, Any]]:
    """Parse an OTLP-JSON ``ExportTraceServiceRequest`` envelope.

    Hex-encoded ids (the OTLP/JSON convention) are normalized to base64 before
    handing the envelope to the protobuf-JSON parser.
    """
    req = ExportTraceServiceRequest()
    Parse(json.dumps(_normalize_json_ids(payload)), req)
    return _parse_request(req)
