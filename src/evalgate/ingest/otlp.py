"""OTLP/HTTP body parsers for `POST /v1/otel/traces`.

Two wire formats are accepted (per OTLP/HTTP spec):

* ``application/x-protobuf`` — `ExportTraceServiceRequest` from
  `opentelemetry-proto`. This is what the official Python OTel SDK ships.
* ``application/json``       — OTLP-JSON envelope:
  ``{"resourceSpans":[{"resource": {...}, "scopeSpans": [{"spans": [...]}]}]}``.

Both paths converge on `(list[Span], resource_attrs: dict)`, where each `Span`
is the internal pydantic model produced by `otel_mapper.map_otel_span`.

Resource attributes are flattened across all ResourceSpans entries in one
payload; if the same key appears with conflicting values, the last one wins
(payloads from a single SDK process always have a single Resource, so this is
fine in practice).
"""

from __future__ import annotations

from typing import Any

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span as PbSpan

from evalgate.core.schemas import Span
from evalgate.ingest.otel_mapper import map_otel_span


def _anyvalue_to_python(v: AnyValue) -> Any:
    """Best-effort unwrap of a protobuf `AnyValue` into a plain Python value."""
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


_OTLP_SPAN_KIND_NAME = {
    PbSpan.SPAN_KIND_UNSPECIFIED: "other",
    PbSpan.SPAN_KIND_INTERNAL: "other",
    PbSpan.SPAN_KIND_SERVER: "other",
    PbSpan.SPAN_KIND_CLIENT: "other",
    PbSpan.SPAN_KIND_PRODUCER: "other",
    PbSpan.SPAN_KIND_CONSUMER: "other",
}


def _pb_span_to_raw(pb: PbSpan) -> dict[str, Any]:
    """Convert a protobuf `Span` into the shape that `map_otel_span` understands."""
    raw: dict[str, Any] = {
        "span_id": pb.span_id.hex(),
        "trace_id": pb.trace_id.hex(),
        "name": pb.name or "unnamed",
        "kind": _OTLP_SPAN_KIND_NAME.get(pb.kind, "other"),
        "start_time_unix_nano": pb.start_time_unix_nano,
        "end_time_unix_nano": pb.end_time_unix_nano,
        "attributes": _kv_list_to_dict(list(pb.attributes)),
    }
    if pb.parent_span_id:
        raw["parent_span_id"] = pb.parent_span_id.hex()
    if pb.HasField("status"):
        # OTLP enum: 0=UNSET / 1=OK / 2=ERROR. Map to strings the internal schema
        # already uses elsewhere; UNSET collapses to OK so reports stay clean.
        code_name = {0: "OK", 1: "OK", 2: "ERROR"}.get(pb.status.code, "OK")
        raw["status"] = {
            "code": code_name,
            "message": pb.status.message or None,
        }
    return raw


def parse_otlp_protobuf(body: bytes) -> tuple[list[Span], dict[str, Any]]:
    """Parse the protobuf body of an OTLP/HTTP trace export request."""
    req = ExportTraceServiceRequest()
    req.ParseFromString(body)

    spans: list[Span] = []
    resource_attrs: dict[str, Any] = {}
    for rs in req.resource_spans:
        resource_attrs.update(_kv_list_to_dict(list(rs.resource.attributes)))
        for ss in rs.scope_spans:
            for pb_span in ss.spans:
                spans.append(map_otel_span(_pb_span_to_raw(pb_span)))
    return spans, resource_attrs


def _otlp_json_resource_attrs(resource: dict[str, Any]) -> dict[str, Any]:
    """OTLP-JSON resource attrs are a `KeyValue` list (same shape as protobuf JSON)."""
    raw = resource.get("attributes") or []
    if isinstance(raw, dict):
        return dict(raw)
    out: dict[str, Any] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        value = item.get("value") or {}
        if not key:
            continue
        # AnyValue is the same union as in otel_mapper._attrs_from_payload — reuse
        # logic locally for resource scope.
        for variant in (
            "stringValue",
            "string_value",
            "intValue",
            "int_value",
            "doubleValue",
            "double_value",
            "boolValue",
            "bool_value",
        ):
            if variant in value:
                out[key] = value[variant]
                break
        else:
            out[key] = value
    return out


def parse_otlp_json(payload: dict[str, Any]) -> tuple[list[Span], dict[str, Any]]:
    """Parse an OTLP-JSON `ExportTraceServiceRequest` envelope."""
    spans: list[Span] = []
    resource_attrs: dict[str, Any] = {}
    for rs in payload.get("resourceSpans", []) or payload.get("resource_spans", []):
        resource_attrs.update(_otlp_json_resource_attrs(rs.get("resource") or {}))
        scope_groups = rs.get("scopeSpans") or rs.get("scope_spans") or []
        for ss in scope_groups:
            for raw_span in ss.get("spans", []):
                spans.append(map_otel_span(raw_span))
    return spans, resource_attrs
