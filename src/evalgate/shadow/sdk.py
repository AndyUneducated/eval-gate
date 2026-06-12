"""Phase 13 Shadow Mode client SDK.

Wrap a production LLM call so a fraction of traffic *also* runs a candidate
prompt (whose output is never returned to the user) and pushes a scored
``(primary, candidate)`` ``EvalRecord`` pair back to EvalGate.

Design contract — the shadow path must never affect the primary request:

* the primary completion is awaited and returned to the caller as usual;
* the candidate run + scoring + HTTP push happen in a background task
  (``asyncio.create_task``), so the caller is never blocked on them;
* the push uses a hard 1s timeout and **swallows every error** — a slow or
  down EvalGate must not slow or break production traffic.

Scoring is SDK-side: we reuse the primary prompt's judge stack
(``build_judge_stack``) to score *both* outputs with the same rubric, then
package each as an ``EvalRecord`` (the public contract the backend +
``build_gate_report`` already consume).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import random as _random
from typing import Any
from uuid import uuid4

import httpx

from evalgate.core.config import is_mock_llm
from evalgate.core.schemas import EvalRecord
from evalgate.judge.candidate import run_candidate
from evalgate.judge.multi_judge import build_judge_stack
from evalgate.judge.prompt_spec import PromptSpec

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 1.0

# Hold strong refs to in-flight background tasks. asyncio only keeps weak
# references to tasks, so without this they can be GC'd mid-flight. Tests also
# await this set to deterministically drain the fire-and-forget path.
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


def _spawn(coro: Any) -> asyncio.Task[Any]:
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def spec_hash(spec: PromptSpec) -> str:
    """Stable content hash of a PromptSpec — the rolling report's group key.

    We hash the canonical JSON dump rather than a file so callers can build
    specs in code; two byte-identical configs collapse to one shadow stream.
    """
    return hashlib.sha256(spec.model_dump_json().encode("utf-8")).hexdigest()


class ShadowClient:
    """Fire-and-forget async HTTP client for ``POST /v1/shadow/observe``."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("EVALGATE_API_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._timeout = timeout
        self._transport = transport

    async def observe(self, payload: dict[str, Any]) -> bool:
        """POST one observation. Returns True on 2xx, False on ANY failure.

        Never raises — the shadow path must not surface into the caller.
        """
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                resp = await client.post("/v1/shadow/observe", json=payload)
                return resp.status_code < 400
        except Exception:
            return False


async def _score(
    judge_stack: Any, case_input: Any, output_text: str, *, mock: bool
) -> tuple[float, float]:
    agg = await judge_stack.score(case_input, output_text, None, mock=mock)
    return agg.score, agg.confidence


def _record(
    *,
    case_id: str,
    tags: list[str],
    score: float,
    confidence: float,
    cost_usd: float,
    latency_ms: int,
) -> EvalRecord:
    return EvalRecord(
        case_id=case_id,
        tags=list(tags),
        score=score,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        judge_confidence=confidence,
    )


async def _shadow_eval_and_push(
    case_input: dict[str, Any],
    *,
    primary: PromptSpec,
    candidate: PromptSpec,
    primary_text: str,
    primary_cost: float,
    primary_latency: int,
    tags: list[str],
    case_id: str,
    client: ShadowClient,
    mock: bool,
) -> bool:
    """Run + score the candidate, then push the pair. Never raises."""
    try:
        cand_mock = "mock-candidate-output" if mock else None
        candidate_out = await run_candidate(case_input, candidate, mock_response=cand_mock)

        # Same rubric for both sides -> a fair primary-vs-candidate comparison.
        judge_stack = build_judge_stack(primary)
        p_score, p_conf = await _score(judge_stack, case_input, primary_text, mock=mock)
        c_score, c_conf = await _score(judge_stack, case_input, candidate_out.text, mock=mock)

        primary_rec = _record(
            case_id=case_id,
            tags=tags,
            score=p_score,
            confidence=p_conf,
            cost_usd=primary_cost,
            latency_ms=primary_latency,
        )
        candidate_rec = _record(
            case_id=case_id,
            tags=tags,
            score=c_score,
            confidence=c_conf,
            cost_usd=candidate_out.cost_usd,
            latency_ms=candidate_out.latency_ms,
        )
        payload = {
            "case_id": case_id,
            "tags": list(tags),
            "primary_prompt_hash": spec_hash(primary),
            "candidate_prompt_hash": spec_hash(candidate),
            "primary": primary_rec.model_dump(),
            "candidate": candidate_rec.model_dump(),
        }
        return await client.observe(payload)
    except Exception:
        # Absolutely never let the shadow path raise into the event loop.
        return False


async def shadow(
    case_input: dict[str, Any],
    *,
    primary: PromptSpec,
    candidate: PromptSpec,
    sample_rate: float = 0.1,
    tags: list[str] | None = None,
    case_id: str | None = None,
    client: ShadowClient | None = None,
    mock: bool | None = None,
    rng: _random.Random | None = None,
) -> str:
    """Run the primary prompt and return its text; shadow-eval the candidate.

    The primary completion is always run and returned to the caller. With
    probability ``sample_rate`` a background task additionally runs + scores
    the candidate and fire-and-forgets the scored pair to EvalGate. The
    background task is never awaited by the caller and can never raise into
    the primary request path.

    Integration is ~3 lines::

        from evalgate.shadow import shadow
        ...
        answer = await shadow(case_input, primary=primary_spec, candidate=candidate_spec)

    Returns the primary candidate's text.
    """
    mock_flag = is_mock_llm() if mock is None else mock
    cand_mock = "mock-candidate-output" if mock_flag else None
    primary_out = await run_candidate(case_input, primary, mock_response=cand_mock)

    roll = (rng or _random).random()
    if roll < sample_rate:
        _spawn(
            _shadow_eval_and_push(
                dict(case_input),
                primary=primary,
                candidate=candidate,
                primary_text=primary_out.text,
                primary_cost=primary_out.cost_usd,
                primary_latency=primary_out.latency_ms,
                tags=list(tags or []),
                case_id=case_id or uuid4().hex,
                client=client or ShadowClient(),
                mock=mock_flag,
            )
        )

    return primary_out.text


async def drain_background_tasks() -> None:
    """Await all in-flight fire-and-forget shadow tasks (tests / graceful shutdown)."""
    if _BACKGROUND_TASKS:
        await asyncio.gather(*list(_BACKGROUND_TASKS), return_exceptions=True)
