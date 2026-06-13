"""eval_set.repository.list_cases status filter (Phase 14).

The runner calls ``list_cases`` with its default, so the default *must* be
active-only — otherwise pending/archived cases would leak into a gate run.
"""

from __future__ import annotations

from uuid import uuid4

from evalgate.core.schemas import CaseStatus
from evalgate.db.models import EvalSetRow
from evalgate.eval_set import repository as set_repo


def _id() -> str:
    return uuid4().hex


async def _seed_mixed(factory) -> str:
    async with factory() as session:
        s = EvalSetRow(id=_id(), name="mixed")
        session.add(s)
        await session.commit()
        set_id = s.id
    async with factory() as session:
        await set_repo.add_case(
            session, set_id=set_id, input={"question": "active?"}, status=CaseStatus.active
        )
        await set_repo.add_case(
            session, set_id=set_id, input={"question": "pending?"}, status=CaseStatus.pending
        )
        await set_repo.add_case(
            session, set_id=set_id, input={"question": "archived?"}, status=CaseStatus.archived
        )
    return set_id


async def test_default_is_active_only(db_session_factory):
    set_id = await _seed_mixed(db_session_factory)
    async with db_session_factory() as session:
        cases = await set_repo.list_cases(session, set_id)
    assert len(cases) == 1
    assert cases[0].status == CaseStatus.active.value


async def test_statuses_none_returns_all(db_session_factory):
    set_id = await _seed_mixed(db_session_factory)
    async with db_session_factory() as session:
        cases = await set_repo.list_cases(session, set_id, statuses=None)
    assert len(cases) == 3


async def test_explicit_status_filter(db_session_factory):
    set_id = await _seed_mixed(db_session_factory)
    async with db_session_factory() as session:
        pending = await set_repo.list_cases(session, set_id, statuses=("pending",))
        both = await set_repo.list_cases(session, set_id, statuses=("pending", "archived"))
    assert {c.status for c in pending} == {"pending"}
    assert {c.status for c in both} == {"pending", "archived"}
