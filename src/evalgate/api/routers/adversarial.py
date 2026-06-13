"""Adversarial Case Synth REST API (Phase 14).

Endpoints (mounted under ``/v1``):

* ``POST /v1/eval-sets/{set_id}/adversarial?tag=<t>&k=10``  generate K pending cases
* ``GET  /v1/eval-sets/{set_id}/adversarial/pending``       list pending adversarial cases
* ``POST /v1/adversarial/{case_id}/review``                 approve / reject a pending case
* ``GET  /v1/eval-sets/{set_id}/adversarial/stats``         hit-rate report

Generation issues generator-LLM calls; ``EVALGATE_MOCK_LLM=1`` (or ``?mock=1``)
forces the deterministic offline path so CI never touches a real model. The
``set_id`` path segment accepts a set id *or* name (resolved by the repo).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.adversarial import repository as adv_repo
from evalgate.api.routers.eval_sets import _case_out
from evalgate.core.config import get_settings, is_mock_llm
from evalgate.core.logging import get_logger
from evalgate.core.schemas import (
    AdversarialReviewRequest,
    AdversarialStatsOut,
    EvalCaseOut,
)
from evalgate.db.session import get_session

log = get_logger("evalgate.api.adversarial")
router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class AdversarialGenerateResponse(BaseModel):
    tag: str
    requested: int
    created: list[EvalCaseOut] = Field(default_factory=list)


@router.post(
    "/eval-sets/{set_id}/adversarial",
    response_model=AdversarialGenerateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_adversarial(
    set_id: str,
    session: SessionDep,
    tag: Annotated[str, Query(min_length=1)],
    k: Annotated[int, Query(ge=1, le=100)] = 10,
    model: Annotated[str | None, Query()] = None,
    mock: Annotated[bool, Query()] = False,
) -> AdversarialGenerateResponse:
    use_mock = mock or is_mock_llm()
    gen_model = model or get_settings().adversarial_generator_model
    created = await adv_repo.generate_into_set(
        session,
        set_id_or_name=set_id,
        tag=tag,
        k=k,
        model=gen_model,
        mock=use_mock,
    )
    log.info("adversarial.generate", set_id=set_id, tag=tag, k=k, created=len(created))
    return AdversarialGenerateResponse(
        tag=tag,
        requested=k,
        created=[_case_out(c) for c in created],
    )


@router.get(
    "/eval-sets/{set_id}/adversarial/pending",
    response_model=list[EvalCaseOut],
)
async def list_pending_adversarial(
    set_id: str,
    session: SessionDep,
) -> list[EvalCaseOut]:
    pending = await adv_repo.list_pending(session, set_id_or_name=set_id)
    return [_case_out(c) for c in pending]


@router.post("/adversarial/{case_id}/review", response_model=EvalCaseOut)
async def review_adversarial(
    case_id: str,
    payload: AdversarialReviewRequest,
    session: SessionDep,
) -> EvalCaseOut:
    row = await adv_repo.review_case(session, case_id=case_id, decision=payload.decision)
    log.info("adversarial.review", case_id=case_id, decision=payload.decision)
    return _case_out(row)


@router.get(
    "/eval-sets/{set_id}/adversarial/stats",
    response_model=AdversarialStatsOut,
)
async def adversarial_stats(
    set_id: str,
    session: SessionDep,
    threshold: Annotated[float, Query(ge=0.0, le=1.0)] = adv_repo.DEFAULT_HIT_THRESHOLD,
) -> AdversarialStatsOut:
    result = await adv_repo.stats(session, set_id_or_name=set_id, threshold=threshold)
    return AdversarialStatsOut(**result.to_dict())
