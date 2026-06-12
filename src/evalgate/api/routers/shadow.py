"""Shadow Mode REST API (Phase 13).

* ``POST /v1/shadow/observe``  — accept one scored ``(primary, candidate)`` pair
                                 from the client SDK; pure write, returns 202.
* ``GET  /v1/shadow/reports``  — compute a rolling 4-axis report live (no persist).
* ``POST /v1/shadow/rollup``   — compute + persist a snapshot + fire regression alert.

The backend stays thin: scoring happens SDK-side, and the rolling aggregation
reuses ``gate.decision.build_gate_report`` (primary records = baseline,
candidate records = candidate) so shadow + PR CI share one gate definition.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.core.schemas import GateReport, ShadowObserveRequest, ShadowReportOut
from evalgate.db.session import get_session
from evalgate.shadow import persistence, rollup

router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/shadow/observe", status_code=status.HTTP_202_ACCEPTED)
async def observe(payload: ShadowObserveRequest, session: SessionDep) -> dict[str, str]:
    row = await persistence.add_observation(
        session,
        case_id=payload.case_id,
        tags=payload.tags,
        primary_prompt_hash=payload.primary_prompt_hash,
        candidate_prompt_hash=payload.candidate_prompt_hash,
        primary_record=payload.primary.model_dump(),
        candidate_record=payload.candidate.model_dump(),
    )
    return {"id": row.id, "status": "accepted"}


@router.get("/shadow/reports", response_model=ShadowReportOut)
async def get_report(
    session: SessionDep,
    candidate_prompt_hash: Annotated[str, Query(description="group key from the SDK")],
    window_hours: Annotated[int, Query(ge=1, le=720)] = 24,
) -> ShadowReportOut:
    report, obs, window_start, window_end = await rollup.compute_live_report(
        session, candidate_prompt_hash, window_hours=window_hours
    )
    return ShadowReportOut(
        candidate_prompt_hash=candidate_prompt_hash,
        window_start=window_start,
        window_end=window_end,
        n_observations=len(obs),
        passed=report.passed,
        report=report,
    )


@router.post("/shadow/rollup", response_model=ShadowReportOut)
async def post_rollup(
    session: SessionDep,
    candidate_prompt_hash: Annotated[str, Query(description="group key from the SDK")],
    window_hours: Annotated[int, Query(ge=1, le=720)] = 24,
) -> ShadowReportOut:
    row = await rollup.run_rollup(session, candidate_prompt_hash, window_hours=window_hours)
    return ShadowReportOut(
        candidate_prompt_hash=row.candidate_prompt_hash,
        window_start=row.window_start,
        window_end=row.window_end,
        n_observations=row.n_observations,
        passed=row.passed,
        report=GateReport.model_validate(row.report),
    )
