"""Phase 13: rolling shadow report computation + rollup persistence/alert."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from evalgate.shadow import persistence, rollup


def _pair(case_id: str, *, cost: float, cand_cost: float, score: float = 0.8) -> tuple[dict, dict]:
    primary = {
        "case_id": case_id,
        "tags": ["billing"],
        "score": score,
        "cost_usd": cost,
        "latency_ms": 800,
    }
    candidate = {**primary, "cost_usd": cand_cost}
    return primary, candidate


async def _seed_regression(s, *, n: int = 40, candidate_hash: str = "cand") -> None:
    for i in range(n):
        primary, candidate = _pair(f"c{i}", cost=0.0020, cand_cost=0.0024)
        await persistence.add_observation(
            s,
            case_id=f"c{i}",
            tags=["billing"],
            primary_prompt_hash="prim",
            candidate_prompt_hash=candidate_hash,
            primary_record=primary,
            candidate_record=candidate,
        )


async def test_compute_live_report_flags_cost_regression(db_session_factory) -> None:
    async with db_session_factory() as s:
        await _seed_regression(s, n=40)
        report, obs, _, _ = await rollup.compute_live_report(s, "cand", window_hours=24)

    assert len(obs) == 40
    axes = {a.name: a for a in report.axes}
    assert axes["cost"].passed is False
    assert axes["cost"].significant is True
    assert axes["cost"].delta > 0  # candidate costs more (lower-is-better)
    assert axes["quality"].passed is True  # identical scores -> within tolerance
    assert report.passed is False


async def test_run_rollup_persists_snapshot_and_alerts(db_session_factory) -> None:
    captured: list = []

    async def alerter(report) -> bool:
        captured.append(report)
        return True

    async with db_session_factory() as s:
        await _seed_regression(s, n=40)
        row = await rollup.run_rollup(s, "cand", window_hours=24, alerter=alerter)

        assert row.passed is False
        assert row.alerted is True
        assert row.n_observations == 40
        assert len(captured) == 1

        snapshots = await persistence.list_reports(s, candidate_prompt_hash="cand")
        assert len(snapshots) == 1
        assert snapshots[0].id == row.id


async def test_run_rollup_no_alert_when_passing(db_session_factory) -> None:
    alerts: list = []

    async def alerter(report) -> bool:  # pragma: no cover - must NOT be called
        alerts.append(report)
        return True

    async with db_session_factory() as s:
        for i in range(20):
            primary, candidate = _pair(f"c{i}", cost=0.002, cand_cost=0.002)
            await persistence.add_observation(
                s,
                case_id=f"c{i}",
                tags=["billing"],
                primary_prompt_hash="prim",
                candidate_prompt_hash="even",
                primary_record=primary,
                candidate_record=candidate,
            )
        row = await rollup.run_rollup(s, "even", window_hours=24, alerter=alerter)

    assert row.passed is True
    assert row.alerted is False
    assert alerts == []


async def test_window_excludes_stale_observations(db_session_factory) -> None:
    async with db_session_factory() as s:
        row = await persistence.add_observation(
            s,
            case_id="old",
            tags=[],
            primary_prompt_hash="prim",
            candidate_prompt_hash="cand",
            primary_record={"case_id": "old", "score": 0.8, "cost_usd": 0.002, "latency_ms": 800},
            candidate_record={"case_id": "old", "score": 0.8, "cost_usd": 0.002, "latency_ms": 800},
        )
        # Backdate it well outside a 24h window.
        row.created_at = datetime.now(UTC) - timedelta(hours=48)
        await s.commit()

        _, obs, _, _ = await rollup.compute_live_report(s, "cand", window_hours=24)
        assert obs == []
