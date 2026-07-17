"""eval_runs / eval_results persistence layer.

Kept separate from `eval_set/repository.py` on purpose: one module owns the
*dataset* (sets + cases), this one owns the *executions* of that dataset.
Both stay dialect-agnostic (no `pg_insert`) so the aiosqlite test fixture
hits the same code paths as production Postgres.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.db.models import EvalJudgeCallRow, EvalResultRow, EvalRunRow
from evalgate.db.query_helpers import new_id as _new_id
from evalgate.judge.protocol import JudgeCallRecord


def _normalise_axis_breakdown(
    breakdown: dict[str, dict[str, float]] | None,
) -> dict[str, dict[str, float]] | None:
    if breakdown is None:
        return None
    cleaned: dict[str, dict[str, float]] = {}
    for axis, metrics in breakdown.items():
        if not isinstance(metrics, dict):
            continue
        cleaned[str(axis)] = {str(k): float(v) for k, v in metrics.items()}
    return cleaned or None


async def create_run(
    session: AsyncSession,
    *,
    eval_set_id: str,
    prompt_path: str,
    prompt_hash: str,
    candidate_model: str,
    judge_model: str,
) -> EvalRunRow:
    row = EvalRunRow(
        id=_new_id(),
        eval_set_id=eval_set_id,
        prompt_path=prompt_path,
        prompt_hash=prompt_hash,
        candidate_model=candidate_model,
        judge_model=judge_model,
        total_cases=0,
        mean_score=None,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def add_result(
    session: AsyncSession,
    *,
    run_id: str,
    case_id: str | None,
    tags: list[str],
    output_text: str,
    score: float,
    reason: str | None,
    cost_usd: float,
    latency_ms: int,
    judge_confidence: float | None = None,
    judge_raw: dict[str, Any] | None = None,
    axis_breakdown: dict[str, dict[str, float]] | None = None,
    retrieved_contexts: list[str] | None = None,
) -> EvalResultRow:
    row = EvalResultRow(
        id=_new_id(),
        eval_run_id=run_id,
        eval_case_id=case_id,
        tags=list(tags or []),
        output={"text": output_text},
        score=float(score),
        reason=reason,
        cost_usd=float(cost_usd),
        latency_ms=int(latency_ms),
        judge_confidence=judge_confidence,
        judge_raw=judge_raw,
        axis_breakdown=_normalise_axis_breakdown(axis_breakdown),
        retrieved_contexts=(
            [str(c) for c in retrieved_contexts] if retrieved_contexts is not None else None
        ),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def finalize_run(
    session: AsyncSession,
    run_id: str,
    *,
    total_cases: int,
    mean_score: float | None,
) -> EvalRunRow | None:
    row = await session.get(EvalRunRow, run_id)
    if row is None:
        return None
    row.total_cases = int(total_cases)
    row.mean_score = mean_score
    await session.commit()
    await session.refresh(row)
    return row


async def get_run(session: AsyncSession, run_id: str) -> EvalRunRow | None:
    return await session.get(EvalRunRow, run_id)


async def add_judge_calls(
    session: AsyncSession,
    *,
    result_id: str,
    calls: list[JudgeCallRecord],
) -> list[EvalJudgeCallRow]:
    """Bulk-insert per-call judge invocations bound to an EvalResultRow.

    Empty `calls` -> no-op (returns []), so callers don't need to guard.
    Single `commit()` per result keeps Phase 15 streaming cheap.
    """
    if not calls:
        return []
    rows = [
        EvalJudgeCallRow(
            id=_new_id(),
            eval_result_id=result_id,
            judge_model=c.judge_model,
            sub_run_index=int(c.sub_run_index),
            position=c.position,
            score=(float(c.score) if c.score is not None else None),
            winner=c.winner,
            reason=c.reason,
            raw=c.raw,
        )
        for c in calls
    ]
    session.add_all(rows)
    await session.commit()
    return rows


async def list_judge_calls(session: AsyncSession, result_id: str) -> list[EvalJudgeCallRow]:
    stmt = (
        select(EvalJudgeCallRow)
        .where(EvalJudgeCallRow.eval_result_id == result_id)
        .order_by(EvalJudgeCallRow.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_results(session: AsyncSession, run_id: str) -> list[EvalResultRow]:
    stmt = (
        select(EvalResultRow)
        .where(EvalResultRow.eval_run_id == run_id)
        .order_by(EvalResultRow.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_runs(
    session: AsyncSession,
    *,
    eval_set_id: str | None = None,
    limit: int = 50,
) -> list[EvalRunRow]:
    """List recent eval_runs (latest first), optionally filtered by eval_set.

    Phase 11: feeds the Streamlit Reports page's run pickers via
    ``GET /v1/runs``. Order is ``created_at DESC`` so "latest baseline /
    candidate" is the natural default; ties (same-second SQLite inserts) fall
    back to ``id DESC`` for deterministic test output.
    """
    stmt = select(EvalRunRow).order_by(EvalRunRow.created_at.desc(), EvalRunRow.id.desc())
    if eval_set_id is not None:
        stmt = stmt.where(EvalRunRow.eval_set_id == eval_set_id)
    stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().all())
