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
from typing import Any

import litellm

# Capture both `score: 0.7`, `score = 0.7`, and `score is 0.7` styles.
_SCORE_RE = re.compile(r'"?score"?\s*(?:[:=]|\bis\b)\s*([0-9]*\.?[0-9]+)', re.IGNORECASE)


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
    if mock_response is not None:
        kwargs["mock_response"] = mock_response
    try:
        resp = await litellm.acompletion(**kwargs)
    except Exception as exc:
        return "", {"error": f"judge-call-failed: {exc}"}
    return _extract_text(resp), _to_dict(resp)


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


def _extract_text(resp: Any) -> str:
    try:
        return resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _to_dict(resp: Any) -> dict[str, Any]:
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
