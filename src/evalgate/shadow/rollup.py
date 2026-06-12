"""Rolling shadow report computation (Phase 13).

The whole point of shadow mode is that production traffic accumulates
``(primary, candidate)`` pairs; rolling those up over a window through the
*same* ``build_gate_report`` surfaces regressions a PR eval set never covered.

``compute_shadow_report`` is a pure function over a list of observation rows;
``compute_live_report`` adds the window filter; ``run_rollup`` persists a
snapshot and fires the regression alert.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from evalgate.core.schemas import GateReport
from evalgate.gate.decision import build_gate_report
from evalgate.shadow import persistence
from evalgate.shadow.alert import maybe_alert

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from evalgate.db.models import ShadowObservationRow, ShadowReportRow

# An injectable async alert hook: takes the (failing) report, returns whether
# an alert was delivered. Lets the smoke / tests assert the alert path without
# a live webhook endpoint.
Alerter = Callable[[GateReport], Awaitable[bool]]


def _as_aware(dt: datetime) -> datetime:
    """Coerce a possibly-naive timestamp (SQLite) to tz-aware UTC."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def compute_shadow_report(observations: list[ShadowObservationRow]) -> GateReport:
    """Aggregate observations into a gate report (primary=baseline, candidate=candidate)."""
    baseline = [dict(o.primary_record or {}) for o in observations]
    candidate = [dict(o.candidate_record or {}) for o in observations]
    return build_gate_report(baseline, candidate)


def _within_window(
    observations: list[ShadowObservationRow], *, window_start: datetime
) -> list[ShadowObservationRow]:
    return [o for o in observations if _as_aware(o.created_at) >= window_start]


async def compute_live_report(
    session: AsyncSession,
    candidate_prompt_hash: str,
    *,
    window_hours: int = 24,
) -> tuple[GateReport, list[ShadowObservationRow], datetime, datetime]:
    """Compute (but don't persist) a rolling report for the trailing window."""
    now = datetime.now(UTC)
    window_start = now - timedelta(hours=window_hours)
    obs = await persistence.list_observations(session, candidate_prompt_hash=candidate_prompt_hash)
    obs = _within_window(obs, window_start=window_start)
    report = compute_shadow_report(obs)
    return report, obs, window_start, now


async def run_rollup(
    session: AsyncSession,
    candidate_prompt_hash: str,
    *,
    window_hours: int = 24,
    webhook_url: str | None = None,
    alerter: Alerter | None = None,
) -> ShadowReportRow:
    """Compute the rolling report, persist a snapshot, and alert on regression."""
    report, obs, window_start, window_end = await compute_live_report(
        session, candidate_prompt_hash, window_hours=window_hours
    )

    alerted = False
    if not report.passed:
        if alerter is not None:
            alerted = await alerter(report)
        else:
            alerted = await maybe_alert(
                report,
                candidate_prompt_hash=candidate_prompt_hash,
                webhook_url=webhook_url,
            )

    return await persistence.add_report(
        session,
        candidate_prompt_hash=candidate_prompt_hash,
        window_start=window_start,
        window_end=window_end,
        n_observations=len(obs),
        passed=report.passed,
        report=report.model_dump(mode="json"),
        alerted=alerted,
    )
