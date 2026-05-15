"""Turn a stored trace (list of SpanRow) into an eval case payload.

Strategy (per Phase 4 plan): pick the **first LLM span** in the trace and
treat its prompt / response as the eval case `input` / `expected`. Anything
fancier (per-step agent evaluation, retrieval-aware promotion) is deliberately
deferred to Phase 7+ BadCase finder.

Pure function, no DB IO — easy to unit-test against any list of span-like
objects.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from evalgate.core.schemas import TaskKind


class SpanLike(Protocol):
    """Structural type covering both `SpanRow` (ORM) and the pydantic `Span`."""

    span_id: str
    name: str
    kind: str
    attributes: dict[str, Any]
    start_time: Any
    parent_span_id: str | None


class NoLLMSpanError(ValueError):
    """Raised when a trace has no recognizable LLM span to promote."""


_LLM_PROMPT_KEYS = ("gen_ai.prompt", "gen_ai.request.messages", "messages", "prompt", "input")
_LLM_RESPONSE_KEYS = (
    "gen_ai.response.content",
    "gen_ai.completion",
    "gen_ai.response",
    "response",
    "output",
)


def _is_llm_span(span: SpanLike) -> bool:
    if (span.attributes.get("evalgate.kind") or "").lower() == "llm":
        return True
    if span.kind == "llm":
        return True
    return any(k.startswith("gen_ai.") for k in span.attributes)


def _pick_field(attrs: dict[str, Any], keys: tuple[str, ...]) -> Any | None:
    for key in keys:
        if key in attrs and attrs[key] not in (None, "", []):
            return attrs[key]
    return None


def _input_payload(span: SpanLike) -> dict[str, Any]:
    explicit = _pick_field(span.attributes, _LLM_PROMPT_KEYS)
    if explicit is not None:
        # Always return a dict so DB JSONB column is happy regardless of
        # what the upstream attribute shape was (list / str / dict).
        return {"prompt": explicit}
    # Fallback: dump every gen_ai.{request,input}.* attr we can find.
    bundle = {
        k: v
        for k, v in span.attributes.items()
        if k.startswith("gen_ai.request.") or k.startswith("gen_ai.input.")
    }
    if bundle:
        return bundle
    # Last resort: the whole attributes dict.
    return dict(span.attributes)


def _expected_payload(span: SpanLike) -> dict[str, Any] | None:
    explicit = _pick_field(span.attributes, _LLM_RESPONSE_KEYS)
    if explicit is None:
        return None
    return {"answer": explicit}


def _infer_task_type(spans: Iterable[SpanLike]) -> TaskKind:
    tool_count = 0
    has_retriever = False
    for s in spans:
        kind = (s.attributes.get("evalgate.kind") or s.kind or "").lower()
        if kind == "retriever":
            has_retriever = True
        elif kind == "tool":
            tool_count += 1
    if has_retriever:
        return TaskKind.rag
    if tool_count >= 2:
        return TaskKind.agent
    return TaskKind.generic


def _root_tags(spans: list[SpanLike]) -> list[str]:
    """Best-effort: pull tags from the root span's `evalgate.tag(s)` attribute."""
    root = next((s for s in spans if not s.parent_span_id), None)
    if root is None:
        return []
    raw = root.attributes.get("evalgate.tags") or root.attributes.get("evalgate.tag")
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(t) for t in raw]
    return []


def extract_case_from_trace(
    spans: list[SpanLike],
    *,
    extra_tags: list[str] | None = None,
    task_type_override: TaskKind | None = None,
) -> dict[str, Any]:
    """Return a dict ready to drop into `EvalCaseRow(**...)`.

    Raises `NoLLMSpanError` if the trace has no LLM span. The caller (router
    or repository) is responsible for translating that to a 422.
    """
    if not spans:
        raise NoLLMSpanError("trace has no spans")

    ordered = sorted(spans, key=lambda s: s.start_time)
    llm_span = next((s for s in ordered if _is_llm_span(s)), None)
    if llm_span is None:
        raise NoLLMSpanError("no LLM span (gen_ai.* / evalgate.kind=llm) found in trace")

    tags = _root_tags(ordered)
    if extra_tags:
        # Preserve order, de-dup case-sensitively.
        seen = set(tags)
        for t in extra_tags:
            if t not in seen:
                tags.append(t)
                seen.add(t)

    task_type = task_type_override or _infer_task_type(ordered)

    return {
        "task_type": str(task_type),
        "input": _input_payload(llm_span),
        "expected": _expected_payload(llm_span),
        "tags": tags,
        "source_span_id": llm_span.span_id,
    }
