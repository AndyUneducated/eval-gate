"""Slack-compatible regression alerts for shadow reports (Phase 13).

A rolling shadow report that regresses on any axis triggers an alert. We POST
a minimal ``{"text": ...}`` body, which is the shape a Slack *incoming
webhook* accepts (and most generic webhook receivers tolerate). No Slack SDK
dependency.

When no webhook URL is configured (``EVALGATE_SHADOW_WEBHOOK_URL`` /
``Settings.shadow_webhook_url``) the alert degrades to a ``structlog`` warning
so local + CI runs never need an external endpoint.
"""

from __future__ import annotations

import httpx

from evalgate.core.config import get_settings
from evalgate.core.logging import get_logger
from evalgate.core.schemas import GateReport

log = get_logger("evalgate.shadow.alert")

_TIMEOUT = 5.0


def format_alert(report: GateReport, *, candidate_prompt_hash: str) -> str:
    """Human-readable alert body (Slack ``text``)."""
    failed = [axis.name for axis in report.axes if not axis.passed]
    lines = [
        f":rotating_light: EvalGate shadow regression — candidate {candidate_prompt_hash[:12]}",
        f"Regressed axes: {', '.join(failed) if failed else 'none'}",
    ]
    if report.summary:
        lines.append(report.summary)
    return "\n".join(lines)


async def send_alert(
    report: GateReport,
    *,
    candidate_prompt_hash: str,
    webhook_url: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    """POST the alert. Returns True on 2xx, False on any failure (never raises)."""
    text = format_alert(report, candidate_prompt_hash=candidate_prompt_hash)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
            resp = await client.post(webhook_url, json={"text": text})
            return resp.status_code < 400
    except Exception as exc:  # network down / bad URL — alerting must not crash rollup
        log.warning(
            "shadow.alert.failed",
            error=str(exc),
            candidate=candidate_prompt_hash,
        )
        return False


async def maybe_alert(
    report: GateReport,
    *,
    candidate_prompt_hash: str,
    webhook_url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> bool:
    """Send the alert if a webhook is configured, else log a warning.

    Returns whether an alert was actually delivered.
    """
    url = webhook_url or get_settings().shadow_webhook_url
    if not url:
        log.warning(
            "shadow.alert.skipped_no_webhook",
            candidate=candidate_prompt_hash,
            summary=report.summary,
        )
        return False
    return await send_alert(
        report,
        candidate_prompt_hash=candidate_prompt_hash,
        webhook_url=url,
        transport=transport,
    )
