"""Runner orchestration: load cases → router.dispatch → persist → yield.

Replaces the Phase 5/6 ``judge.runner.iter_eval`` (now deleted). The
orchestration shape is the same — async generator producing one
``EvalRecord`` per case, with a thin ``run_eval`` drainer for callers
that don't care about streaming. What changed:

- The "candidate call + MultiJudge score" body moved into
  :class:`evalgate.evaluator.generic.GenericEvaluator`.
- Per-case dispatch goes through :class:`EvaluatorRouter`, so RAG (and
  later Agent) cases land in their own evaluator.
- Persistence now writes ``axis_breakdown`` and ``retrieved_contexts``
  (Phase 8/10 columns) when the evaluator returns them.
- Phase 10: a ``SafetyPipeline.augment`` hook runs after ``evaluator.evaluate``
  and merges ``axis_breakdown["safety"]`` before persistence, so generic /
  rag / agent paths all carry the safety
  axis without each evaluator caring.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.core.config import is_mock_llm
from evalgate.core.schemas import EvalRecord
from evalgate.eval_set import repository as eval_set_repo
from evalgate.evaluator.base import EvaluationOutcome, UnsupportedTaskTypeError
from evalgate.evaluator.router import EvaluatorRouter, build_router
from evalgate.judge import persistence
from evalgate.judge.prompt_spec import PromptSpec, hash_prompt, load_prompt_spec
from evalgate.safety.pipeline import SafetyPipeline, build_safety_pipeline


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
    return is_mock_llm()


async def _persist_outcome(
    session: AsyncSession,
    *,
    run_id: str,
    case_id: str,
    tags: list[str],
    outcome: EvaluationOutcome,
) -> Any:
    result_row = await persistence.add_result(
        session,
        run_id=run_id,
        case_id=case_id,
        tags=tags,
        output_text=outcome.output_text,
        score=outcome.score,
        reason=outcome.reason,
        cost_usd=outcome.cost_usd,
        latency_ms=outcome.latency_ms,
        judge_confidence=outcome.confidence,
        judge_raw=outcome.judge_raw,
        axis_breakdown=outcome.axis_breakdown,
        retrieved_contexts=outcome.retrieved_contexts,
    )
    if outcome.raw_calls:
        await persistence.add_judge_calls(session, result_id=result_row.id, calls=outcome.raw_calls)
    return result_row


async def iter_eval(
    session: AsyncSession,
    *,
    eval_set_id: str,
    spec: PromptSpec,
    run_id: str,
    router: EvaluatorRouter,
    mock: bool = False,
    limit: int | None = None,
    safety_pipeline: SafetyPipeline | None = None,
) -> AsyncIterator[EvalRecord]:
    """Yield one ``EvalRecord`` per case, persisting along the way.

    ``safety_pipeline`` is the Phase 10 hook. ``run_eval`` builds it once per
    run from ``spec.safety``; passing ``None`` disables safety scoring entirely
    (useful for tests that don't care).
    """
    cases = await eval_set_repo.list_cases(session, eval_set_id)
    if limit is not None:
        cases = cases[:limit]

    for case in cases:
        tags = list(case.tags or [])
        try:
            evaluator = router.for_case(case)
        except UnsupportedTaskTypeError as exc:
            outcome = EvaluationOutcome(
                score=0.0,
                output_text="",
                cost_usd=0.0,
                latency_ms=0,
                judge_raw={"error": f"unsupported_task_type: {exc}"},
                reason=str(exc),
                error=True,
                error_kind="unsupported_task_type",
            )
        else:
            outcome = await evaluator.evaluate(case, mock=mock)

        if safety_pipeline is not None:
            outcome = await safety_pipeline.augment(case, outcome, mock=mock)

        result_row = await _persist_outcome(
            session,
            run_id=run_id,
            case_id=case.id,
            tags=tags,
            outcome=outcome,
        )

        yield EvalRecord(
            case_id=case.id,
            tags=tags,
            score=outcome.score,
            cost_usd=outcome.cost_usd,
            latency_ms=outcome.latency_ms,
            axis_breakdown=outcome.axis_breakdown,
            judge_confidence=outcome.confidence,
            error=outcome.error,
            error_kind=outcome.error_kind,
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
    """Resolve set + load prompt + drive ``iter_eval`` + finalise run."""
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

    use_mock = _mock_enabled(mock)
    router = build_router(spec, mock=use_mock)
    safety_pipeline = build_safety_pipeline(spec, mock=use_mock)

    run = await persistence.create_run(
        session,
        eval_set_id=set_id,
        prompt_path=str(prompt_path),
        prompt_hash=hash_prompt(prompt_path),
        candidate_model=spec.candidate.model,
        judge_model=router.label(),
    )

    records: list[EvalRecord] = []
    async for rec in iter_eval(
        session,
        eval_set_id=set_id,
        spec=spec,
        run_id=run.id,
        router=router,
        mock=use_mock,
        limit=limit,
        safety_pipeline=safety_pipeline,
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
