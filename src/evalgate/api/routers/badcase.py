"""BadCase REST API.

Endpoints (mounted under ``/v1``):

* ``GET /v1/badcases``                                      list badcases by strategy
* ``POST /v1/badcases/{eval_result_id}/promote``            copy underlying case to a target set

`GET` is read-only and side-effect-free except for the ``llm`` strategy, which
issues cheap-model LLM calls. ``EVALGATE_MOCK_LLM=1`` forces fully-offline.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.badcase import finder
from evalgate.badcase import repository as badcase_repo
from evalgate.core.config import is_mock_llm
from evalgate.core.logging import get_logger
from evalgate.core.schemas import BadCaseOut, PromotionOut
from evalgate.db.session import get_session

log = get_logger("evalgate.api.badcase")
router = APIRouter()
SessionDep = Annotated[AsyncSession, Depends(get_session)]

_VALID_STRATEGIES = ("uncertainty", "outlier", "llm")


class BadCaseListResponse(BaseModel):
    strategy: str
    items: list[BadCaseOut] = Field(default_factory=list)


class PromoteRequest(BaseModel):
    target_set: str
    strategy: str | None = None
    extra_tags: list[str] = Field(default_factory=list)


def _badcase_out(bc: finder.BadCase) -> BadCaseOut:
    return BadCaseOut(**bc.to_dict())


def _promotion_out(row: Any) -> PromotionOut:
    return PromotionOut.model_validate(row)


def _mock_enabled() -> bool:
    return is_mock_llm()


@router.get("/badcases", response_model=BadCaseListResponse)
async def list_badcases(
    session: SessionDep,
    strategy: Annotated[str, Query()] = "uncertainty",
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    run_id: Annotated[str | None, Query()] = None,
    mock: Annotated[bool, Query()] = False,
) -> BadCaseListResponse:
    if strategy not in _VALID_STRATEGIES:
        raise HTTPException(
            status_code=422,
            detail=f"strategy must be one of {_VALID_STRATEGIES}, got {strategy!r}",
        )
    use_mock = mock or _mock_enabled()
    items = await finder.find(
        session,
        strategy=strategy,
        run_id=run_id,
        limit=limit,
        mock=use_mock,
    )
    log.info(
        "badcase.list",
        strategy=strategy,
        run_id=run_id,
        limit=limit,
        returned=len(items),
    )
    return BadCaseListResponse(strategy=strategy, items=[_badcase_out(bc) for bc in items])


@router.post(
    "/badcases/{eval_result_id}/promote",
    response_model=PromotionOut,
    status_code=status.HTTP_201_CREATED,
)
async def promote_badcase(
    eval_result_id: str,
    payload: PromoteRequest,
    session: SessionDep,
) -> PromotionOut:
    membership = await badcase_repo.promote_result_to_set(
        session,
        eval_result_id=eval_result_id,
        target_set_id_or_name=payload.target_set,
        strategy=payload.strategy,
        extra_tags=payload.extra_tags,
    )
    log.info(
        "badcase.promote",
        eval_result_id=eval_result_id,
        target_set=payload.target_set,
        membership_id=membership.id,
        eval_case_id=membership.eval_case_id,
    )
    return _promotion_out(membership)
