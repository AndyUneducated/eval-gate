"""Map OTel/OTLP span payloads into the internal `Span` model.

Accepts two shapes for now:
  1. A simplified flat dict (snake_case) for tests + SDK ergonomics.
  2. OTLP-JSON span body (camelCase + attribute key/value list).

Full OTLP `ResourceSpans` envelope parsing is a follow-up.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from evalgate.core.schemas import Span, SpanKind

_OTEL_KIND_INT_TO_HINT = {
    0: SpanKind.other,
    1: SpanKind.other,  # INTERNAL
    2: SpanKind.other,  # SERVER
    3: SpanKind.other,  # CLIENT
    4: SpanKind.other,  # PRODUCER
    5: SpanKind.other,  # CONSUMER
}


def _normalize_kind(raw: Any) -> SpanKind:
    if raw is None:
        return SpanKind.other
    if isinstance(raw, int):
        return _OTEL_KIND_INT_TO_HINT.get(raw, SpanKind.other)
    try:
        return SpanKind(str(raw).lower())
    except ValueError:
        return SpanKind.other


def _attrs_from_payload(value: Any) -> dict[str, Any]:
    """Accept either a plain dict (simplified) or an OTLP attribute list."""
    if not value:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, list):
        return {}

    out: dict[str, Any] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        wrapped = item.get("value", {}) or {}
        if not key or not isinstance(wrapped, dict):
            continue
        # AnyValue union — pick whichever variant is present.
        for variant, caster in (
            ("stringValue", str),
            ("string_value", str),
            ("intValue", int),
            ("int_value", int),
            ("doubleValue", float),
            ("double_value", float),
            ("boolValue", bool),
            ("bool_value", bool),
        ):
            if variant in wrapped:
                raw_val = wrapped[variant]
                try:
                    out[key] = caster(raw_val)
                except (TypeError, ValueError):
                    out[key] = raw_val
                break
        else:
            out[key] = wrapped
    return out


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1e9, tz=UTC)
    if isinstance(value, str):
        # nanos-as-string (OTLP) vs ISO-8601.
        if value.isdigit():
            return datetime.fromtimestamp(int(value) / 1e9, tz=UTC)
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"unparseable timestamp: {value!r}") from exc
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    raise ValueError(f"unsupported timestamp type: {type(value).__name__}")


def map_otel_span(raw: dict[str, Any]) -> Span:
    """Convert a single OTel/OTLP span dict into the internal `Span` model."""
    span_id = raw.get("span_id") or raw.get("spanId")
    trace_id = raw.get("trace_id") or raw.get("traceId")
    if not span_id or not trace_id:
        raise ValueError("span requires both 'span_id' and 'trace_id'")

    parent = raw.get("parent_span_id") or raw.get("parentSpanId")
    name = raw.get("name") or "unnamed"

    attributes = _attrs_from_payload(raw.get("attributes"))
    kind_hint = raw.get("kind") or attributes.get("evalgate.kind")
    kind = _normalize_kind(kind_hint)

    start_ts = _parse_timestamp(
        raw.get("start_time") or raw.get("start_time_unix_nano") or raw.get("startTimeUnixNano")
    )
    end_ts = _parse_timestamp(
        raw.get("end_time") or raw.get("end_time_unix_nano") or raw.get("endTimeUnixNano")
    )
    if start_ts is None or end_ts is None:
        raise ValueError("span requires both start and end timestamps")

    status_block = raw.get("status") or {}
    if isinstance(status_block, dict):
        status_code = str(status_block.get("code") or "OK")
        status_message = status_block.get("message")
    else:
        status_code, status_message = "OK", None

    return Span(
        span_id=str(span_id),
        trace_id=str(trace_id),
        parent_span_id=str(parent) if parent else None,
        name=name,
        kind=kind,
        start_time=start_ts,
        end_time=end_ts,
        attributes=attributes,
        status_code=status_code,
        status_message=status_message,
    )
