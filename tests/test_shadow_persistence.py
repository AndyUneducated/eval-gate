"""Phase 13: shadow_observations / shadow_reports persistence."""

from __future__ import annotations

from datetime import UTC, datetime

from evalgate.shadow import persistence


async def test_add_and_list_observations_filters_by_candidate_hash(db_session_factory) -> None:
    async with db_session_factory() as s:
        await persistence.add_observation(
            s,
            case_id="c1",
            tags=["billing"],
            primary_prompt_hash="prim",
            candidate_prompt_hash="cand",
            primary_record={"case_id": "c1", "score": 0.8},
            candidate_record={"case_id": "c1", "score": 0.7},
        )
        await persistence.add_observation(
            s,
            case_id="c2",
            tags=[],
            primary_prompt_hash="prim",
            candidate_prompt_hash="other",
            primary_record={"case_id": "c2", "score": 0.9},
            candidate_record={"case_id": "c2", "score": 0.9},
        )

        only_cand = await persistence.list_observations(s, candidate_prompt_hash="cand")
        assert len(only_cand) == 1
        assert only_cand[0].case_id == "c1"
        assert only_cand[0].primary_record["score"] == 0.8
        assert only_cand[0].candidate_record["score"] == 0.7

        everything = await persistence.list_observations(s)
        assert {o.case_id for o in everything} == {"c1", "c2"}


async def test_add_and_list_reports(db_session_factory) -> None:
    async with db_session_factory() as s:
        now = datetime.now(UTC)
        await persistence.add_report(
            s,
            candidate_prompt_hash="cand",
            window_start=now,
            window_end=now,
            n_observations=42,
            passed=False,
            report={"passed": False, "summary": "Regressed axes: cost."},
            alerted=True,
        )
        # A second hash should not show up when filtering.
        await persistence.add_report(
            s,
            candidate_prompt_hash="other",
            window_start=now,
            window_end=now,
            n_observations=1,
            passed=True,
            report={"passed": True},
            alerted=False,
        )

        rows = await persistence.list_reports(s, candidate_prompt_hash="cand")
        assert len(rows) == 1
        row = rows[0]
        assert row.passed is False
        assert row.alerted is True
        assert row.n_observations == 42
        assert row.report["summary"] == "Regressed axes: cost."

        assert len(await persistence.list_reports(s)) == 2
