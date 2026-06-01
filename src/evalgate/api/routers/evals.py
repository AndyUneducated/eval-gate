"""Eval-run REST API.

* ``POST /v1/evals/run``                 — runs the multi-axis gate over a baseline / candidate
                                          pair of `EvalRecord` payloads and returns a GateReport.
* ``GET  /v1/runs``                      — list eval_runs (latest first, optional eval_set filter).
* ``GET  /v1/runs/{run_id}``             — single eval_run meta.
* ``GET  /v1/runs/{run_id}/records``     — per-case `EvalRecord` payloads for a run; this is the
                                          shape `POST /v1/evals/run` expects, so the UI can pipe
                                          two runs straight into the gate.

Phase 11 added the read-only `/v1/runs*` endpoints so the Streamlit UI never
talks to the database directly: it picks two runs, fetches their records, and
POSTs them into ``/v1/evals/run`` to build a GateReport. The judge runner that
*produces* these records lives one layer up (CLI / future async worker).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.core.schemas import EvalRecord, GateReport
from evalgate.db.models import EvalResultRow, EvalRunRow
from evalgate.db.session import get_session
from evalgate.gate.decision import build_gate_report
from evalgate.judge import persistence

router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class EvalRunRequest(BaseModel):
    baseline: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-case eval records from the baseline (current main) run.",
    )
    candidate: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Per-case eval records from the candidate (PR head) run.",
    )


class EvalRunOut(BaseModel):
    """API response shape for an `eval_runs` row.

    Mirrors `EvalRunRow` 1:1; the UI's run-picker on the Reports page renders
    this as `<created_at> · <prompt_path> · <candidate_model>`.
    """

    id: str
    eval_set_id: str
    prompt_path: str
    prompt_hash: str
    candidate_model: str
    judge_model: str
    total_cases: int
    mean_score: float | None
    created_at: datetime


class EvalRunListResponse(BaseModel):
    runs: list[EvalRunOut] = Field(default_factory=list)


class EvalRunRecordsResponse(BaseModel):
    """`EvalRecord`-shaped per-case payload for a run.

    The gate (`POST /v1/evals/run`) consumes lists of `EvalRecord`-shaped
    dicts; this endpoint hands those out directly so the UI can wire two
    runs straight into the gate without re-implementing the mapping.
    """

    run_id: str
    records: list[EvalRecord] = Field(default_factory=list)


def _run_out(row: EvalRunRow) -> EvalRunOut:
    return EvalRunOut(
        id=row.id,
        eval_set_id=row.eval_set_id,
        prompt_path=row.prompt_path,
        prompt_hash=row.prompt_hash,
        candidate_model=row.candidate_model,
        judge_model=row.judge_model,
        total_cases=row.total_cases,
        mean_score=row.mean_score,
        created_at=row.created_at,
    )


def _result_to_record(row: EvalResultRow) -> EvalRecord:
    """Map an `EvalResultRow` (DB) → `EvalRecord` (gate input) shape.

    `EvalRecord` is ``extra="allow"`` so we can stash row-only fields
    (``eval_result_id`` / ``output_text`` / ``retrieved_contexts``) in a
    forward-compatible way; today's gate ignores extras but Phase 12+ tag
    attribution against trace fields will read them.
    """
    output_text = ""
    if isinstance(row.output, dict):
        output_text = str(row.output.get("text", "") or "")
    return EvalRecord(
        case_id=row.eval_case_id or row.id,
        tags=list(row.tags or []),
        score=float(row.score),
        cost_usd=float(row.cost_usd),
        latency_ms=int(row.latency_ms),
        axis_breakdown=row.axis_breakdown,
        eval_result_id=row.id,
        eval_run_id=row.eval_run_id,
        judge_confidence=row.judge_confidence,
        retrieved_contexts=list(row.retrieved_contexts or []) if row.retrieved_contexts else [],
        output_text=output_text,
    )


@router.post("/evals/run", response_model=GateReport)
async def run_evals(payload: EvalRunRequest) -> GateReport:
    return build_gate_report(payload.baseline, payload.candidate)


@router.get("/runs", response_model=EvalRunListResponse)
async def list_runs(
    session: SessionDep,
    eval_set_id: Annotated[str | None, Query(description="filter by eval_set id")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> EvalRunListResponse:
    rows = await persistence.list_runs(session, eval_set_id=eval_set_id, limit=limit)
    return EvalRunListResponse(runs=[_run_out(r) for r in rows])


@router.get("/runs/{run_id}", response_model=EvalRunOut)
async def get_run(run_id: str, session: SessionDep) -> EvalRunOut:
    row = await persistence.get_run(session, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"eval_run {run_id!r} not found")
    return _run_out(row)


@router.get("/runs/{run_id}/records", response_model=EvalRunRecordsResponse)
async def get_run_records(run_id: str, session: SessionDep) -> EvalRunRecordsResponse:
    row = await persistence.get_run(session, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"eval_run {run_id!r} not found")
    results = await persistence.list_results(session, run_id)
    return EvalRunRecordsResponse(
        run_id=run_id,
        records=[_result_to_record(r) for r in results],
    )
