"""OTLP/HTTP body parsers for `POST /v1/otel/traces`.

Protobuf and OTLP-JSON both parse into ``ExportTraceServiceRequest``, then
share one code path into internal ``Span`` models via ``map_otel_span``.
"""

from __future__ import annotations

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
    if pb.parent_span_id:
        raw["parent_span_id"] = pb.parent_span_id.hex()
    if pb.HasField("status"):
        code_name = {0: "OK", 1: "OK", 2: "ERROR"}.get(pb.status.code, "OK")
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


def parse_otlp_json(payload: dict[str, Any]) -> tuple[list[Span], dict[str, Any]]:
    """Parse an OTLP-JSON ``ExportTraceServiceRequest`` envelope."""
    req = ExportTraceServiceRequest()
    Parse(json.dumps(payload), req)
    return _parse_request(req)
