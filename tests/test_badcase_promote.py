"""promote_result_to_set: insert a membership row, don't duplicate the case.

Invariants covered (Phase 4.5 + 7.5 membership model):

- New ``EvalCaseSetMembershipRow`` with strategy, extra_tags,
  promoted_from_result_id.
- Source case is NOT duplicated (still exactly one ``EvalCaseRow`` with
  that id).
- Source case's intrinsic tags / payload are untouched.
- ``list_cases(target_set)`` surfaces the promoted case.
- Re-promote (same case, same target) -> ``AlreadyPromotedError``.
- Promoting into the case's *originating* set is the same error class —
  Phase 4.5 collapsed "same set" into the unified uniqueness invariant.
- Missing eval_result / dangling eval_case -> ``BadCaseNotFoundError``.
- Unknown target set -> ``EvalSetNotFoundError``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from evalgate.badcase import repository as badcase_repo
from evalgate.db.models import (
    EvalCaseRow,
    EvalCaseSetMembershipRow,
    EvalResultRow,
    EvalRunRow,
    EvalSetRow,
)
from evalgate.eval_set import repository as set_repo


def _id() -> str:
    return uuid4().hex


async def _seed(db_session_factory, *, with_case: bool = True):
    """Seed: two sets, one case primary-member of `src`, one run+result.

    The case is added via `add_case` so its membership in `src` is the
    real production path (Phase 4.5: membership row, no `eval_set_id`
    column on the case).
    """
    async with db_session_factory() as session:
        src_set = EvalSetRow(id=_id(), name="src")
        dst_set = EvalSetRow(id=_id(), name="dst")
        session.add_all([src_set, dst_set])
        await session.commit()
        src_id, dst_id = src_set.id, dst_set.id

    case_id: str | None = None
    if with_case:
        async with db_session_factory() as session:
            case = await set_repo.add_case(
                session,
                set_id=src_id,
                task_type="generic",
                input={"prompt": "hi"},
                expected={"output": "ref"},
                tags=["billing", "v1"],
                source_trace_id="tr-1",
                source_span_id="sp-1",
            )
            case_id = case.id

    async with db_session_factory() as session:
        run = EvalRunRow(
            id=_id(),
            eval_set_id=src_id,
            prompt_path="p.yaml",
            prompt_hash="h" * 64,
            candidate_model="m",
            judge_model="j",
        )
        session.add(run)
        await session.commit()

        result = EvalResultRow(
            id=_id(),
            eval_run_id=run.id,
            eval_case_id=case_id,
            tags=["billing"],
            output={"text": "out"},
            score=0.4,
            cost_usd=0.0,
            latency_ms=10,
            judge_confidence=0.2,
        )
        session.add(result)
        await session.commit()
        return src_id, dst_id, result.id, case_id


@pytest.mark.asyncio
async def test_promote_creates_membership_without_duplicating_case(db_session_factory):
    src_id, dst_id, result_id, src_case_id = await _seed(db_session_factory)

    async with db_session_factory() as session:
        membership = await badcase_repo.promote_result_to_set(
            session,
            eval_result_id=result_id,
            target_set_id_or_name="dst",
            strategy="uncertainty",
            extra_tags=["needs-review", "needs-review"],  # dedupe check
        )

    assert membership.eval_case_id == src_case_id
    assert membership.eval_set_id == dst_id
    assert membership.promoted_from_result_id == result_id
    assert membership.strategy == "uncertainty"
    assert membership.tags == ["needs-review"]  # deduped

    # Exactly one EvalCaseRow ever — promote doesn't duplicate payload.
    async with db_session_factory() as session:
        rows = list((await session.execute(select(EvalCaseRow))).scalars().all())
    assert len(rows) == 1
    assert rows[0].id == src_case_id
    assert rows[0].tags == ["billing", "v1"]  # source tags untouched

    # Two memberships: the originating one (src) + the promoted one (dst).
    async with db_session_factory() as session:
        memberships = await set_repo.list_memberships(session, case_id=src_case_id)
    assert {m.eval_set_id for m in memberships} == {src_id, dst_id}
    # Quiet the unused `src_id` warning for readers.
    assert src_id != dst_id


@pytest.mark.asyncio
async def test_list_cases_surfaces_promoted_case_in_target_set(db_session_factory):
    src_id, dst_id, result_id, src_case_id = await _seed(db_session_factory)

    async with db_session_factory() as session:
        await badcase_repo.promote_result_to_set(
            session,
            eval_result_id=result_id,
            target_set_id_or_name="dst",
            strategy="uncertainty",
        )

    async with db_session_factory() as session:
        dst_cases = await set_repo.list_cases(session, dst_id)
        src_cases = await set_repo.list_cases(session, src_id)
    assert [c.id for c in dst_cases] == [src_case_id]
    assert [c.id for c in src_cases] == [src_case_id]


@pytest.mark.asyncio
async def test_re_promote_raises_already_promoted(db_session_factory):
    _, _, result_id, _ = await _seed(db_session_factory)
    async with db_session_factory() as session:
        await badcase_repo.promote_result_to_set(
            session, eval_result_id=result_id, target_set_id_or_name="dst"
        )
    async with db_session_factory() as session:
        with pytest.raises(badcase_repo.AlreadyPromotedError):
            await badcase_repo.promote_result_to_set(
                session, eval_result_id=result_id, target_set_id_or_name="dst"
            )

    # Only the originating + the one successful promote membership exist.
    async with db_session_factory() as session:
        rows = list((await session.execute(select(EvalCaseSetMembershipRow))).scalars().all())
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_promote_into_origin_set_is_already_promoted(db_session_factory):
    """Phase 4.5: the originating set is just another membership.

    Attempting to "promote" a case back into its own set raises
    AlreadyPromotedError — there's no special "same-set" case anymore.
    """
    src_id, _, result_id, _ = await _seed(db_session_factory)
    async with db_session_factory() as session:
        with pytest.raises(badcase_repo.AlreadyPromotedError):
            await badcase_repo.promote_result_to_set(
                session,
                eval_result_id=result_id,
                target_set_id_or_name=src_id,
            )


@pytest.mark.asyncio
async def test_promote_missing_result(db_session_factory):
    await _seed(db_session_factory)
    async with db_session_factory() as session:
        with pytest.raises(badcase_repo.BadCaseNotFoundError):
            await badcase_repo.promote_result_to_set(
                session,
                eval_result_id="nonexistent",
                target_set_id_or_name="dst",
            )


@pytest.mark.asyncio
async def test_promote_result_without_case(db_session_factory):
    """A result row that lost its source case (eval_case_id=None) can't be promoted."""
    _, _, result_id, _ = await _seed(db_session_factory, with_case=False)
    async with db_session_factory() as session:
        with pytest.raises(badcase_repo.BadCaseNotFoundError):
            await badcase_repo.promote_result_to_set(
                session,
                eval_result_id=result_id,
                target_set_id_or_name="dst",
            )


@pytest.mark.asyncio
async def test_promote_unknown_target_set(db_session_factory):
    _, _, result_id, _ = await _seed(db_session_factory)
    async with db_session_factory() as session:
        with pytest.raises(set_repo.EvalSetNotFoundError):
            await badcase_repo.promote_result_to_set(
                session,
                eval_result_id=result_id,
                target_set_id_or_name="ghost",
            )
