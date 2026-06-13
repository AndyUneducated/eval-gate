"""adversarial.repository: generate (pending) -> review -> stats lifecycle."""

from __future__ import annotations

from uuid import uuid4

import pytest

from evalgate.adversarial import repository as adv_repo
from evalgate.core.schemas import CaseSource, CaseStatus
from evalgate.db.models import EvalResultRow, EvalRunRow, EvalSetRow
from evalgate.eval_set import repository as set_repo


def _id() -> str:
    return uuid4().hex


async def _seed_set(factory, *, tag: str = "billing", n: int = 3) -> str:
    async with factory() as session:
        s = EvalSetRow(id=_id(), name="billing-set")
        session.add(s)
        await session.commit()
        set_id = s.id
    async with factory() as session:
        for i in range(n):
            await set_repo.add_case(
                session,
                set_id=set_id,
                input={"question": f"base case {i}"},
                expected={"answer": "a"},
                tags=[tag],
            )
    return set_id


async def _add_result(factory, *, set_id: str, case_id: str, score: float) -> None:
    async with factory() as session:
        run = EvalRunRow(
            id=_id(),
            eval_set_id=set_id,
            prompt_path="p",
            prompt_hash="h" * 64,
            candidate_model="m",
            judge_model="j",
        )
        session.add(run)
        await session.commit()
        session.add(
            EvalResultRow(
                id=_id(),
                eval_run_id=run.id,
                eval_case_id=case_id,
                tags=["billing", "adversarial"],
                output={"text": "o"},
                score=score,
                cost_usd=0.0,
                latency_ms=1,
            )
        )
        await session.commit()


async def test_generate_inserts_pending_adversarial_cases(db_session_factory):
    set_id = await _seed_set(db_session_factory)
    async with db_session_factory() as session:
        created = await adv_repo.generate_into_set(
            session, set_id_or_name=set_id, tag="billing", k=6, mock=True
        )
    assert len(created) == 6
    for c in created:
        assert c.status == CaseStatus.pending.value
        assert c.source == CaseSource.adversarial.value
        assert "billing" in c.tags
        assert "adversarial" in c.tags


async def test_generate_accepts_set_name(db_session_factory):
    await _seed_set(db_session_factory)
    async with db_session_factory() as session:
        created = await adv_repo.generate_into_set(
            session, set_id_or_name="billing-set", tag="billing", k=2, mock=True
        )
    assert len(created) == 2


async def test_runner_view_excludes_pending(db_session_factory):
    """The default ``list_cases`` (what the runner uses) hides pending cases."""
    set_id = await _seed_set(db_session_factory, n=3)
    async with db_session_factory() as session:
        await adv_repo.generate_into_set(
            session, set_id_or_name=set_id, tag="billing", k=5, mock=True
        )
    async with db_session_factory() as session:
        active = await set_repo.list_cases(session, set_id)  # default active-only
        all_cases = await set_repo.list_cases(session, set_id, statuses=None)
    assert len(active) == 3  # only the base cases
    assert len(all_cases) == 8  # base + 5 pending adversarial
    assert all(c.source != CaseSource.adversarial.value for c in active)


async def test_list_pending_only_returns_adversarial_pending(db_session_factory):
    set_id = await _seed_set(db_session_factory)
    async with db_session_factory() as session:
        await adv_repo.generate_into_set(
            session, set_id_or_name=set_id, tag="billing", k=4, mock=True
        )
    async with db_session_factory() as session:
        pending = await adv_repo.list_pending(session, set_id_or_name=set_id)
    assert len(pending) == 4
    assert all(p.source == CaseSource.adversarial.value for p in pending)


async def test_review_approve_then_reject(db_session_factory):
    set_id = await _seed_set(db_session_factory)
    async with db_session_factory() as session:
        created = await adv_repo.generate_into_set(
            session, set_id_or_name=set_id, tag="billing", k=2, mock=True
        )
    approve_id, reject_id = created[0].id, created[1].id

    async with db_session_factory() as session:
        approved = await adv_repo.review_case(session, case_id=approve_id, decision="approve")
        rejected = await adv_repo.review_case(session, case_id=reject_id, decision="reject")
    assert approved.status == CaseStatus.active.value
    assert rejected.status == CaseStatus.archived.value

    async with db_session_factory() as session:
        active = await set_repo.list_cases(session, set_id)
        pending = await adv_repo.list_pending(session, set_id_or_name=set_id)
    assert approve_id in {c.id for c in active}
    assert reject_id not in {c.id for c in active}
    assert pending == []


async def test_review_unknown_case_raises(db_session_factory):
    await _seed_set(db_session_factory)
    async with db_session_factory() as session:
        with pytest.raises(adv_repo.CaseNotFoundError):
            await adv_repo.review_case(session, case_id="nope", decision="approve")


async def test_review_bad_decision_raises(db_session_factory):
    set_id = await _seed_set(db_session_factory)
    async with db_session_factory() as session:
        created = await adv_repo.generate_into_set(
            session, set_id_or_name=set_id, tag="billing", k=1, mock=True
        )
    async with db_session_factory() as session:
        with pytest.raises(ValueError):
            await adv_repo.review_case(session, case_id=created[0].id, decision="maybe")


async def test_stats_hit_rate_over_approved_adversarial(db_session_factory):
    set_id = await _seed_set(db_session_factory)
    async with db_session_factory() as session:
        created = await adv_repo.generate_into_set(
            session, set_id_or_name=set_id, tag="billing", k=4, mock=True
        )
    # Approve all 4.
    async with db_session_factory() as session:
        for c in created:
            await adv_repo.review_case(session, case_id=c.id, decision="approve")

    # Score 3 below threshold (hits) and 1 above; leave none unevaluated.
    await _add_result(db_session_factory, set_id=set_id, case_id=created[0].id, score=0.1)
    await _add_result(db_session_factory, set_id=set_id, case_id=created[1].id, score=0.2)
    await _add_result(db_session_factory, set_id=set_id, case_id=created[2].id, score=0.49)
    await _add_result(db_session_factory, set_id=set_id, case_id=created[3].id, score=0.9)

    async with db_session_factory() as session:
        st = await adv_repo.stats(session, set_id_or_name=set_id)
    assert st.total == 4
    assert st.evaluated == 4
    assert st.hits == 3
    assert st.hit_rate == pytest.approx(0.75)
    assert st.threshold == 0.5


async def test_stats_uses_latest_result_per_case(db_session_factory):
    set_id = await _seed_set(db_session_factory)
    async with db_session_factory() as session:
        created = await adv_repo.generate_into_set(
            session, set_id_or_name=set_id, tag="billing", k=1, mock=True
        )
    async with db_session_factory() as session:
        await adv_repo.review_case(session, case_id=created[0].id, decision="approve")
    # First a hit, then a later passing result -> latest wins (no hit).
    await _add_result(db_session_factory, set_id=set_id, case_id=created[0].id, score=0.1)
    await _add_result(db_session_factory, set_id=set_id, case_id=created[0].id, score=0.95)
    async with db_session_factory() as session:
        st = await adv_repo.stats(session, set_id_or_name=set_id)
    assert st.evaluated == 1
    assert st.hits == 0


async def test_stats_ignores_pending_and_unevaluated(db_session_factory):
    set_id = await _seed_set(db_session_factory)
    async with db_session_factory() as session:
        created = await adv_repo.generate_into_set(
            session, set_id_or_name=set_id, tag="billing", k=3, mock=True
        )
    # Approve only one; leave two pending. The approved one has no results.
    async with db_session_factory() as session:
        await adv_repo.review_case(session, case_id=created[0].id, decision="approve")
    async with db_session_factory() as session:
        st = await adv_repo.stats(session, set_id_or_name=set_id)
    assert st.total == 1  # only the approved (active) adversarial case
    assert st.evaluated == 0
    assert st.hits == 0
    assert st.hit_rate == 0.0
