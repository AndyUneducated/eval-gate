"""Phase 6 runner: stream `EvalRecord`s out of an (eval_set, prompt.yaml).

Design notes:

- `iter_eval` is an `async` generator yielding one `EvalRecord` per case AS it
  is produced. Phase 16 (Sequential Gate) will consume that stream and decide
  early-stop without us refactoring the runner.
- `run_eval` is a thin wrapper that drains the stream and finalises the run
  with aggregate stats (mean_score, total_cases).
- Cases run sequentially; within a single case the MultiJudge fan-out
  (N x K x P calls) runs concurrently via internal `Semaphore`s.
- We never raise out of a single case: per-case failure becomes a score=0
  record (with ``error=True``) so one bad call cannot poison the whole run.
- Pairwise mode requires `case.expected`. Missing -> we emit a skip record
  rather than silently fall back to pointwise, because that would mask
  ill-prepared eval sets.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.core.schemas import EvalRecord
from evalgate.eval_set import repository as eval_set_repo
from evalgate.judge import persistence
from evalgate.judge.candidate import run_candidate
from evalgate.judge.multi_judge import MultiJudge, build_judge_stack
from evalgate.judge.prompt_spec import PromptSpec, hash_prompt, load_prompt_spec
from evalgate.judge.protocol import stringify


@dataclass
class RunResult:
    run_id: str
    eval_set_id: str
    total_cases: int
    mean_score: float | None
    records: list[EvalRecord]


def _mock_enabled(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    return os.environ.get("EVALGATE_MOCK_LLM", "").lower() in {"1", "true", "yes"}


def _reference_text(expected: dict[str, Any] | None) -> str | None:
    """Best-effort: read `expected.output` (Phase 3 convention from
    `case_extract`); fall back to JSON-stringify of the whole expected dict."""
    if not expected:
        return None
    out = expected.get("output")
    if isinstance(out, str) and out.strip():
        return out
    if out is not None:
        return stringify(out)
    return stringify(expected)


def _judge_models_label(spec: PromptSpec) -> str:
    """`EvalRunRow.judge_model` is a single string column; we join multiple
    judge models with `+` (e.g. ``ollama/qwen2.5:7b+ollama/qwen2.5:32b``).
    Phase 14 won't grep this — it uses `eval_judge_calls.judge_model`."""
    return "+".join(j.model for j in spec.judges)


async def iter_eval(
    session: AsyncSession,
    *,
    eval_set_id: str,
    spec: PromptSpec,
    run_id: str,
    judge_stack: MultiJudge,
    mock: bool = False,
    limit: int | None = None,
) -> AsyncIterator[EvalRecord]:
    """Yield one `EvalRecord` per case, persisting each result + judge calls
    as we go. `run_id` must already exist (see `persistence.create_run`)."""
    cases = await eval_set_repo.list_cases(session, eval_set_id)
    if limit is not None:
        cases = cases[:limit]

    cand_mock = "mock-candidate-output" if mock else None
    mode = spec.judge_policy.mode

    for case in cases:
        case_input = dict(case.input or {})
        tags = list(case.tags or [])
        reference = _reference_text(case.expected)

        # pairwise mode is hard-dependent on a reference answer; fail fast.
        if mode == "pairwise" and not reference:
            result_row = await persistence.add_result(
                session,
                run_id=run_id,
                case_id=case.id,
                tags=tags,
                output_text="",
                score=0.0,
                reason="pairwise mode requires case.expected (skipped)",
                cost_usd=0.0,
                latency_ms=0,
                judge_confidence=None,
                judge_raw={"error": "missing_reference"},
            )
            yield EvalRecord(
                case_id=case.id,
                tags=tags,
                score=0.0,
                cost_usd=0.0,
                latency_ms=0,
                safety_violation=False,
                judge_confidence=None,
                error=True,
                error_kind="missing_reference",
                eval_result_id=result_row.id,
            )
            continue

        # Per-judge mock score plan (mock mode only): N x K constant 0.5s, so
        # downstream parsing/persistence still has well-formed JSON to chew on.
        mock_scores_per_judge: list[list[float]] | None = None
        if mock:
            mock_scores_per_judge = [[0.5] * spec.judge_policy.k for _ in spec.judges]

        try:
            candidate = await run_candidate(case_input, spec, mock_response=cand_mock)
            agg = await judge_stack.score(
                case_input,
                candidate.text,
                reference,
                mock_scores_per_judge=mock_scores_per_judge,
            )
            output_text = candidate.text
            cost_usd = candidate.cost_usd
            latency_ms = candidate.latency_ms
            score = agg.score
            confidence = agg.confidence
            votes = agg.votes
            raw_calls = agg.raw_calls
            judge_raw: dict[str, Any] = {
                "votes": votes,
                "per_judge_confidence": agg.per_judge_confidence,
                "mode": mode,
            }
            reason = None
            error = False
            error_kind: str | None = None
        except Exception as exc:
            output_text = ""
            cost_usd = 0.0
            latency_ms = 0
            score = 0.0
            confidence = None
            raw_calls = []
            judge_raw = {"error": f"runner-failure: {exc}"}
            reason = f"runner-failure: {exc}"
            error = True
            error_kind = "runner_failure"

        result_row = await persistence.add_result(
            session,
            run_id=run_id,
            case_id=case.id,
            tags=tags,
            output_text=output_text,
            score=score,
            reason=reason,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            judge_confidence=confidence,
            judge_raw=judge_raw,
        )
        if raw_calls:
            await persistence.add_judge_calls(session, result_id=result_row.id, calls=raw_calls)

        yield EvalRecord(
            case_id=case.id,
            tags=tags,
            score=score,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            safety_violation=False,
            judge_confidence=confidence,
            error=error,
            error_kind=error_kind,
            eval_result_id=result_row.id,
        )


async def run_eval(
    session: AsyncSession,
    *,
    eval_set_id_or_name: str,
    prompt_path: str,
    judge_model_override: str | None = None,
    k_override: int | None = None,
    concurrency_override: int | None = None,
    policy_mode_override: str | None = None,
    limit: int | None = None,
    mock: bool | None = None,
) -> RunResult:
    """Resolve set + load prompt + drain `iter_eval` + finalise run.

    `mock` precedence: explicit `mock=True/False` > `EVALGATE_MOCK_LLM=1` > real call.

    The ``*_override`` knobs let the variance-experiment script sweep K /
    policy-mode without authoring a new YAML each time.
    """
    set_id = await eval_set_repo.resolve_set_id(session, eval_set_id_or_name)
    spec = load_prompt_spec(prompt_path)

    if judge_model_override:
        new_judges = [j.model_copy(update={"model": judge_model_override}) for j in spec.judges]
        spec = spec.model_copy(update={"judges": new_judges})

    policy_updates: dict[str, Any] = {}
    if k_override is not None:
        policy_updates["k"] = k_override
    if concurrency_override is not None:
        policy_updates["concurrency"] = concurrency_override
    if policy_mode_override is not None:
        policy_updates["mode"] = policy_mode_override
    if policy_updates:
        spec = spec.model_copy(
            update={"judge_policy": spec.judge_policy.model_copy(update=policy_updates)}
        )

    run = await persistence.create_run(
        session,
        eval_set_id=set_id,
        prompt_path=str(prompt_path),
        prompt_hash=hash_prompt(prompt_path),
        candidate_model=spec.candidate.model,
        judge_model=_judge_models_label(spec),
    )

    judge_stack = build_judge_stack(spec)

    use_mock = _mock_enabled(mock)
    records: list[EvalRecord] = []
    async for rec in iter_eval(
        session,
        eval_set_id=set_id,
        spec=spec,
        run_id=run.id,
        judge_stack=judge_stack,
        mock=use_mock,
        limit=limit,
    ):
        records.append(rec)

    mean = sum(r.score for r in records) / len(records) if records else None
    await persistence.finalize_run(
        session,
        run.id,
        total_cases=len(records),
        mean_score=mean,
    )

    return RunResult(
        run_id=run.id,
        eval_set_id=set_id,
        total_cases=len(records),
        mean_score=mean,
        records=records,
    )
