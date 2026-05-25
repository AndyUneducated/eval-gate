"""Candidate LLM call wrapper.

Wraps `litellm.acompletion(...)` with three jobs:
1. render messages via `PromptSpec.render_messages`,
2. measure wall-clock latency (perf_counter),
3. ask LiteLLM for `cost_usd` and gracefully fall back to 0.0 when the model
   has no published pricing (e.g. local Ollama).

We accept an `mock_response: str | None` kwarg so the runner / CLI can force
fully-offline execution (CI, unit tests) without touching this module's logic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import litellm

from evalgate.judge.prompt_spec import CandidateSpec, PromptSpec
from evalgate.judge.protocol import thinking_off_kwargs


@dataclass
class CandidateOutput:
    text: str
    latency_ms: int
    cost_usd: float
    raw: dict[str, Any]


async def run_candidate(
    case_input: dict[str, Any],
    spec: PromptSpec,
    *,
    mock_response: str | None = None,
) -> CandidateOutput:
    """Run the candidate LLM once for a given case input.

    `mock_response`, when set, bypasses any real network call via
    LiteLLM's mock mode — used by CI and unit tests.
    """
    cand: CandidateSpec = spec.candidate
    messages = spec.render_messages(case_input)

    kwargs: dict[str, Any] = {
        "model": cand.model,
        "messages": messages,
        **cand.params,
    }
    for key, value in thinking_off_kwargs(cand.model).items():
        kwargs.setdefault(key, value)
    if mock_response is not None:
        kwargs["mock_response"] = mock_response

    t0 = time.perf_counter()
    resp = await litellm.acompletion(**kwargs)
    latency_ms = int((time.perf_counter() - t0) * 1000)

    text = _extract_text(resp)
    cost_usd = _safe_cost(resp)
    raw = _to_dict(resp)
    return CandidateOutput(text=text, latency_ms=latency_ms, cost_usd=cost_usd, raw=raw)


def _extract_text(resp: Any) -> str:
    try:
        return resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _safe_cost(resp: Any) -> float:
    """`litellm.completion_cost` raises for models with no published pricing
    (e.g. `ollama/*`). Treat that as 'free' rather than crashing the runner."""
    try:
        return float(litellm.completion_cost(completion_response=resp) or 0.0)
    except Exception:
        return 0.0


def _to_dict(resp: Any) -> dict[str, Any]:
    """LiteLLM responses are pydantic-like; coerce to a JSON-serialisable dict
    so we can stash it in `eval_results.judge_raw` / candidate logs."""
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
