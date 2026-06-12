"""Phase 13: Slack-compatible shadow regression alerts."""

from __future__ import annotations

import json

import httpx

from evalgate.core.schemas import AxisMetric, GateReport
from evalgate.shadow import alert


def _failing_report() -> GateReport:
    return GateReport(
        passed=False,
        axes=[
            AxisMetric(
                name="cost",
                baseline=0.002,
                candidate=0.0024,
                delta=0.0004,
                significant=True,
                passed=False,
            ),
            AxisMetric(name="quality", baseline=0.8, candidate=0.8, delta=0.0, passed=True),
        ],
        summary="Regressed axes: cost.",
    )


def test_format_alert_names_failed_axes_and_hash() -> None:
    text = alert.format_alert(_failing_report(), candidate_prompt_hash="abcdef123456789xyz")
    assert "cost" in text
    assert "abcdef123456" in text  # first 12 chars of the hash
    assert "Regressed axes: cost." in text


async def test_send_alert_posts_slack_text_payload() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200)

    ok = await alert.send_alert(
        _failing_report(),
        candidate_prompt_hash="abc123",
        webhook_url="http://hook.test/incoming",
        transport=httpx.MockTransport(handler),
    )
    assert ok is True
    assert captured["url"] == "http://hook.test/incoming"
    assert "text" in captured["body"]
    assert "cost" in captured["body"]["text"]


async def test_send_alert_returns_false_on_error_status() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    ok = await alert.send_alert(
        _failing_report(),
        candidate_prompt_hash="abc123",
        webhook_url="http://hook.test/incoming",
        transport=httpx.MockTransport(handler),
    )
    assert ok is False


async def test_maybe_alert_noop_without_webhook(monkeypatch) -> None:
    class _NoWebhook:
        shadow_webhook_url = None

    monkeypatch.setattr(alert, "get_settings", lambda: _NoWebhook())
    delivered = await alert.maybe_alert(_failing_report(), candidate_prompt_hash="abc")
    assert delivered is False


async def test_maybe_alert_uses_explicit_webhook(monkeypatch) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200)

    delivered = await alert.maybe_alert(
        _failing_report(),
        candidate_prompt_hash="abc",
        webhook_url="http://hook.test/x",
        transport=httpx.MockTransport(handler),
    )
    assert delivered is True
    assert "text" in seen["body"]
