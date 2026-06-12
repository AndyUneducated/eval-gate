"""Phase 13: the client SDK (sampling, fire-and-forget, record shape)."""

from __future__ import annotations

import random

import httpx

from evalgate.judge.prompt_spec import PromptSpec
from evalgate.shadow import sdk


def _spec(name: str = "p") -> PromptSpec:
    return PromptSpec.model_validate(
        {
            "name": name,
            "candidate": {"model": "ollama/qwen3.5:9b", "user_template": "{question}"},
            "judges": [{"model": "ollama/qwen3.5:9b", "rubric": "score it"}],
            "judge_policy": {"mode": "pointwise", "k": 1, "position_swap": False},
            "safety": {"enabled": False},
        }
    )


class _CapturingClient:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def observe(self, payload: dict) -> bool:
        self.payloads.append(payload)
        return True


class _BoomClient:
    async def observe(self, payload: dict) -> bool:
        raise RuntimeError("network down")


async def test_shadow_returns_primary_and_pushes_when_sampled() -> None:
    client = _CapturingClient()
    text = await sdk.shadow(
        {"question": "hi"},
        primary=_spec("primary"),
        candidate=_spec("candidate"),
        sample_rate=1.0,
        tags=["billing"],
        case_id="c1",
        client=client,
        mock=True,
        rng=random.Random(0),
    )
    assert text == "mock-candidate-output"

    await sdk.drain_background_tasks()
    assert len(client.payloads) == 1
    payload = client.payloads[0]
    assert payload["case_id"] == "c1"
    assert payload["tags"] == ["billing"]
    assert payload["primary_prompt_hash"] != payload["candidate_prompt_hash"]
    # mock judge returns 0.5 for both sides; mock candidate has no published cost.
    assert payload["primary"]["score"] == 0.5
    assert payload["candidate"]["score"] == 0.5
    assert payload["primary"]["cost_usd"] == 0.0
    assert payload["primary"]["case_id"] == "c1"


async def test_shadow_skips_push_when_not_sampled() -> None:
    client = _CapturingClient()
    text = await sdk.shadow(
        {"question": "hi"},
        primary=_spec(),
        candidate=_spec(),
        sample_rate=0.0,
        client=client,
        mock=True,
    )
    await sdk.drain_background_tasks()
    assert text == "mock-candidate-output"
    assert client.payloads == []


async def test_shadow_swallows_push_errors() -> None:
    # A failing push must never surface into the caller / event loop.
    text = await sdk.shadow(
        {"question": "hi"},
        primary=_spec(),
        candidate=_spec(),
        sample_rate=1.0,
        client=_BoomClient(),
        mock=True,
        rng=random.Random(0),
    )
    assert text == "mock-candidate-output"
    await sdk.drain_background_tasks()  # must not raise


async def test_shadow_client_observe_status_codes() -> None:
    def ok_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"id": "x", "status": "accepted"})

    ok_client = sdk.ShadowClient(base_url="http://test", transport=httpx.MockTransport(ok_handler))
    assert await ok_client.observe({"a": 1}) is True

    def err_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    err_client = sdk.ShadowClient(
        base_url="http://test", transport=httpx.MockTransport(err_handler)
    )
    assert await err_client.observe({"a": 1}) is False


def test_spec_hash_is_stable_and_content_addressed() -> None:
    assert sdk.spec_hash(_spec("a")) == sdk.spec_hash(_spec("a"))
    assert sdk.spec_hash(_spec("a")) != sdk.spec_hash(_spec("b"))
