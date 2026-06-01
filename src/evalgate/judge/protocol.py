"""Shared LiteLLM call shell + parsing utilities for all Judge variants.

Pointwise and Pairwise judges differ in *prompt + output schema*, not in how
they talk to LiteLLM. This module owns:
- the response-format hinting (JSON when supported),
- the JSON-then-regex tolerant parser (used by pointwise),
- the JudgeCallRecord dataclass that maps 1:1 to `eval_judge_calls` rows.

We never raise out of a judge call: any litellm-side exception turns into a
`JudgeCallRecord` with `score=None / winner=None / reason="..."` so that one
flaky case cannot poison the whole run.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import litellm

# `drop_params=True` lets LiteLLM silently strip provider-unsupported kwargs
# (e.g. `think` reaching OpenAI). Without it, the thinking-off helper below
# would have to maintain an exhaustive "what does provider X accept" table.
# We pay nothing for it on the providers we *do* know — they accept the keys.
litellm.drop_params = True

# Capture both `score: 0.7`, `score = 0.7`, and `score is 0.7` styles.
_SCORE_RE = re.compile(r'"?score"?\s*(?:[:=]|\bis\b)\s*([0-9]*\.?[0-9]+)', re.IGNORECASE)

# Max stdev of scores in [0, 1] (half at 0, half at 1). Used by self-consistency
# and multi-judge confidence spread terms.
MAX_STD_SCORE_SPREAD = 0.5


# Provider-specific kwargs that force thinking OFF. Applied via `setdefault`
# at every LLM call site, so user-supplied `params: {think: true}` (Ollama)
# or `params: {reasoning_effort: medium}` (Anthropic / OpenAI / Gemini) still
# wins and opts the model back into thinking. Unknown providers get an empty
# dict; `litellm.drop_params=True` above handles the case where a future
# provider lands with a different knob name.
#
# Why default OFF: every judge / safety / badcase classifier in EvalGate
# expects strict JSON in a small `num_predict` budget. Qwen3.x on Ollama
# burns the entire budget on `<think>` content and returns empty `content`,
# which our parsers tolerate as `score=0.0` — silently poisoning a run.
# Tested 2026-05 against LiteLLM docs for each prefix below.
_THINKING_OFF_BY_PREFIX: tuple[tuple[str, dict[str, Any]], ...] = (
    ("ollama/", {"think": False}),
    ("anthropic/", {"reasoning_effort": "none"}),
    ("claude-", {"reasoning_effort": "none"}),
    # OpenAI reasoning models can't be hard-disabled; `minimal` is the
    # cheapest still-thinking budget the API exposes.
    ("openai/o", {"reasoning_effort": "minimal"}),
    ("openai/gpt-5", {"reasoning_effort": "minimal"}),
    ("gemini/gemini-2.5", {"reasoning_effort": "none"}),
    ("gemini/gemini-3", {"reasoning_effort": "none"}),
)


def thinking_off_kwargs(model: str) -> dict[str, Any]:
    """Return provider-specific kwargs that turn thinking OFF for ``model``.

    Apply via ``kwargs.setdefault(...)`` so any value already supplied by
    the user (typically through ``params:`` in prompt.yaml) wins. Returns
    an empty dict for providers we don't model — combined with the global
    ``litellm.drop_params=True`` that's a safe no-op.
    """
    m = model.lower()
    for prefix, kwargs in _THINKING_OFF_BY_PREFIX:
        if m.startswith(prefix):
            return dict(kwargs)
    return {}


@dataclass
class LeafVerdict:
    """Unified return from a leaf judge (pointwise or position-swap)."""

    score: float
    agreement: bool | None
    calls: list[JudgeCallRecord]


class LeafJudge(Protocol):
    """PointwiseJudge or PositionSwapJudge — same ``score`` contract."""

    model: str

    async def score(
        self,
        case_input: Any,
        candidate_output: str,
        reference_output: str | None,
        *,
        sub_run_index: int,
        mock: bool,
    ) -> LeafVerdict: ...


@dataclass
class JudgeCallRecord:
    """One raw judge LLM invocation.

    Mirrors `EvalJudgeCallRow` 1:1 so the runner can `add_judge_calls(...)`
    without an extra mapping layer. `position` / `winner` stay None for
    pointwise calls; `score` stays None for pairwise leaf calls (only the
    swap-aggregated row carries a 0/0.5/1 score).
    """

    judge_model: str
    sub_run_index: int
    position: str | None = None
    score: float | None = None
    winner: str | None = None
    reason: str | None = None
    raw: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


async def acompletion_json(
    *,
    model: str,
    messages: list[dict[str, str]],
    params: dict[str, Any] | None = None,
    mock_response: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Call litellm and return ``(text, raw_dict)``.

    Always asks for JSON when the provider supports it; harmless otherwise.
    Returns ``("", {})`` on transport failure rather than raising — callers
    decide how to mark the per-case failure.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        **(params or {}),
    }
    kwargs.setdefault("response_format", {"type": "json_object"})
    for key, value in thinking_off_kwargs(model).items():
        kwargs.setdefault(key, value)
    if mock_response is not None:
        kwargs["mock_response"] = mock_response
    try:
        resp = await litellm.acompletion(**kwargs)
    except Exception as exc:
        return "", {"error": f"judge-call-failed: {exc}"}
    return extract_text(resp), to_dict(resp)


def parse_score(text: str) -> tuple[float, str]:
    """JSON-first parser for pointwise judge output.

    Layers, in order:
      1. ``json.loads`` -> ``{"score": ..., "reason": ...}``
      2. regex on ``score: <num>`` anywhere in text
      3. give up -> ``(0.0, text)`` (truncated)

    Always returns a clamped ``[0,1]`` score.
    """
    if not text:
        return 0.0, ""

    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and "score" in payload:
            return clamp_score(payload["score"]), str(payload.get("reason") or "")
    except (json.JSONDecodeError, TypeError):
        pass

    m = _SCORE_RE.search(text)
    if m:
        return clamp_score(m.group(1)), text.strip()[:500]

    return 0.0, text.strip()[:500]


def parse_winner(text: str) -> tuple[str | None, str]:
    """JSON-first parser for pairwise judge output.

    Expects ``{"winner": "A"|"B"|"tie", "reason": "..."}``. Falls back to
    case-insensitive regex on the raw text. Unknown -> ``(None, raw_text)``.
    """
    if not text:
        return None, ""
    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and "winner" in payload:
            w = _normalise_winner(payload["winner"])
            return w, str(payload.get("reason") or "")
    except (json.JSONDecodeError, TypeError):
        pass

    m = re.search(r"\b(A|B|tie|draw)\b", text, re.IGNORECASE)
    if m:
        return _normalise_winner(m.group(1)), text.strip()[:500]
    return None, text.strip()[:500]


def _normalise_winner(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    if v == "a":
        return "A"
    if v == "b":
        return "B"
    if v in {"tie", "draw"}:
        return "tie"
    return None


def clamp_score(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v != v:  # NaN
        return 0.0
    return max(0.0, min(1.0, v))


def stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def extract_text(resp: Any) -> str:
    try:
        return resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def to_dict(resp: Any) -> dict[str, Any]:
    if hasattr(resp, "model_dump"):
        try:
            return resp.model_dump()
        except Exception:
            pass
    if isinstance(resp, dict):
        return dict(resp)
    try:
        return dict(resp)
    except Exception:
        return {"repr": repr(resp)}
