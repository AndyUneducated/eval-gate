"""Eval-set REST API.

Endpoints (mounted under `/v1`):

* ``POST /v1/eval-sets``                                            create a new set
* ``GET  /v1/eval-sets``                                            list sets (freebie for demo)
* ``GET  /v1/eval-sets/{set_id}``                                   set meta + all its cases
* ``POST /v1/eval-sets/{set_id}/cases``                             add a case explicitly
* ``POST /v1/eval-sets/{set_id}/cases/from-trace/{trace_id}``       promote a trace into a case

The `{set_id}` path param accepts either a UUID hex or a set name; this is the
convenience that makes the CLI feel natural (`--set billing-regress`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.core.logging import get_logger
from evalgate.core.schemas import EvalCaseOut, EvalSetDetail, EvalSetOut, TaskKind
from evalgate.db.session import get_session
from evalgate.eval_set import repository

log = get_logger("evalgate.api.eval_sets")
router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class CreateEvalSetRequest(BaseModel):
    name: str
    description: str | None = None


class CreateCaseRequest(BaseModel):
    task_type: TaskKind = TaskKind.generic
    input: dict[str, Any]
    expected: dict[str, Any] | None = None
    tags: list[str] = Field(default_factory=list)
    source_trace_id: str | None = None
    source_span_id: str | None = None


class FromTraceRequest(BaseModel):
    tags: list[str] = Field(default_factory=list)
    task_type: TaskKind | None = None


class EvalSetListResponse(BaseModel):
    eval_sets: list[EvalSetOut]


def _set_out(row) -> EvalSetOut:
    return EvalSetOut(
        id=row.id,
        name=row.name,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _case_out(row) -> EvalCaseOut:
    return EvalCaseOut(
        id=row.id,
        eval_set_id=row.eval_set_id,
        task_type=TaskKind(row.task_type),
        input=row.input,
        expected=row.expected,
        tags=list(row.tags or []),
        source_trace_id=row.source_trace_id,
        source_span_id=row.source_span_id,
        created_at=row.created_at,
    )


@router.post("/eval-sets", response_model=EvalSetOut, status_code=status.HTTP_201_CREATED)
async def create_eval_set(payload: CreateEvalSetRequest, session: SessionDep) -> EvalSetOut:
    row = await repository.create_eval_set(
        session, name=payload.name, description=payload.description
    )
    log.info("eval_set.create", id=row.id, name=row.name)
    return _set_out(row)


@router.get("/eval-sets", response_model=EvalSetListResponse)
async def list_eval_sets(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    since: Annotated[datetime | None, Query()] = None,
) -> EvalSetListResponse:
    rows = await repository.list_eval_sets(session, limit=limit, since=since)
    return EvalSetListResponse(eval_sets=[_set_out(r) for r in rows])


@router.get("/eval-sets/{set_id}", response_model=EvalSetDetail)
async def get_eval_set_detail(set_id: str, session: SessionDep) -> EvalSetDetail:
    try:
        resolved = await repository.resolve_set_id(session, set_id)
    except repository.EvalSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    set_row = await repository.get_eval_set(session, resolved)
    cases = await repository.list_cases(session, resolved)
    base = _set_out(set_row)
    return EvalSetDetail(
        **base.model_dump(),
        cases=[_case_out(c) for c in cases],
    )


@router.post(
    "/eval-sets/{set_id}/cases",
    response_model=EvalCaseOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_case(set_id: str, payload: CreateCaseRequest, session: SessionDep) -> EvalCaseOut:
    try:
        resolved = await repository.resolve_set_id(session, set_id)
        row = await repository.add_case(
            session,
            set_id=resolved,
            task_type=payload.task_type,
            input=payload.input,
            expected=payload.expected,
            tags=payload.tags,
            source_trace_id=payload.source_trace_id,
            source_span_id=payload.source_span_id,
        )
    except repository.EvalSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _case_out(row)


@router.post(
    "/eval-sets/{set_id}/cases/from-trace/{trace_id}",
    response_model=EvalCaseOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_case_from_trace(
    set_id: str,
    trace_id: str,
    session: SessionDep,
    payload: FromTraceRequest | None = None,
) -> EvalCaseOut:
    payload = payload or FromTraceRequest()
    try:
        resolved = await repository.resolve_set_id(session, set_id)
        row = await repository.add_case_from_trace(
            session,
            set_id=resolved,
            trace_id=trace_id,
            extra_tags=payload.tags,
            task_type_override=payload.task_type,
        )
    except repository.EvalSetNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.TraceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.NoLLMSpanError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _case_out(row)
