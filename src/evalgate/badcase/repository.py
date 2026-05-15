"""Promote a badcase (an ``eval_result``) into a target eval_set.

Phase 4.5 model: a case's set membership is entirely expressed by rows
in ``eval_case_set_memberships``. Promote inserts one membership row
pointing at the originating case, preserving full lineage
(``promoted_from_result_id`` + ``strategy`` + ``tags``) without payload
duplication.

Properties:

- input/expected stay single-sourced; if the canonical case ever needs an
  edit (typo, schema migration), there's exactly one row to change.
- "Same-set" promotion is no longer a special case — the originating set
  is just another membership; trying to promote into it surfaces the
  generic ``AlreadyPromotedError`` (HTTP 409).
- Duplicate-promote is structurally impossible: the
  ``UniqueConstraint(eval_case_id, eval_set_id)`` is the source of truth.

Errors raised (mapped to HTTP in `api.routers.badcase`):
- ``BadCaseNotFoundError``     -> 404, result row / case row missing.
- ``AlreadyPromotedError``     -> 409, membership already exists.
- ``eval_set.EvalSetNotFoundError`` -> 404, unknown target set.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.db.models import (
    EvalCaseRow,
    EvalCaseSetMembershipRow,
    EvalResultRow,
)
from evalgate.eval_set import repository as set_repo


class BadCaseNotFoundError(LookupError):
    """Raised when promote() can't resolve eval_result -> eval_case."""


class AlreadyPromotedError(ValueError):
    """Raised when the case is already a member of the target set.

    Covers both the "promoted twice into the same destination" case and
    the "tried to promote into the case's originating set" case — both
    are structurally the same after Phase 4.5.
    """


def _new_id() -> str:
    return uuid4().hex


async def promote_result_to_set(
    session: AsyncSession,
    *,
    eval_result_id: str,
    target_set_id_or_name: str,
    strategy: str | None = None,
    extra_tags: list[str] | None = None,
) -> EvalCaseSetMembershipRow:
    """Add the eval_case behind ``eval_result_id`` to the target set.

    Returns the freshly-inserted ``EvalCaseSetMembershipRow``. The caller
    can look up the case payload via ``EvalCaseSetMembershipRow.eval_case_id``
    if needed.
    """
    result = await session.get(EvalResultRow, eval_result_id)
    if result is None:
        raise BadCaseNotFoundError(f"no eval_result with id {eval_result_id!r}")
    if not result.eval_case_id:
        raise BadCaseNotFoundError(f"eval_result {eval_result_id!r} has no source eval_case")
    src_case = await session.get(EvalCaseRow, result.eval_case_id)
    if src_case is None:
        raise BadCaseNotFoundError(f"source eval_case {result.eval_case_id!r} was deleted")

    target_set_id = await set_repo.resolve_set_id(session, target_set_id_or_name)

    # Pre-check rather than relying on IntegrityError so the message is
    # explicit and we don't have to roll back a dirty session.
    existing_stmt = select(EvalCaseSetMembershipRow).where(
        EvalCaseSetMembershipRow.eval_case_id == src_case.id,
        EvalCaseSetMembershipRow.eval_set_id == target_set_id,
    )
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        raise AlreadyPromotedError(
            f"case {src_case.id!r} is already a member of set {target_set_id!r} "
            f"(membership {existing.id!r})"
        )

    membership = EvalCaseSetMembershipRow(
        id=_new_id(),
        eval_case_id=src_case.id,
        eval_set_id=target_set_id,
        promoted_from_result_id=eval_result_id,
        strategy=strategy,
        tags=list(dict.fromkeys(extra_tags or [])),  # dedupe, preserve order
    )
    session.add(membership)
    await session.commit()
    await session.refresh(membership)
    return membership
