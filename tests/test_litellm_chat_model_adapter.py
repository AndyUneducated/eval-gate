"""Phase 8: LiteLLMChatModel + LiteLLMEmbeddings adapter shape.

These verify the langchain↔LiteLLM bridge without going to the network:

- chat model with ``mock_text`` short-circuits and returns a single
  ChatGeneration carrying that exact text;
- chat model in non-mock mode delegates to ``litellm.acompletion`` with
  the right ``messages`` payload;
- embeddings in mock mode produce stable hash vectors of the right
  dimension.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from evalgate.evaluator.rag.ragas_adapter import (
    LiteLLMChatModel,
    LiteLLMEmbeddings,
)


@pytest.mark.asyncio
async def test_chat_model_mock_text_short_circuits():
    chat = LiteLLMChatModel(model="ollama/qwen2.5:7b", mock_text='{"score": 0.7}')
    result = await chat._agenerate([HumanMessage(content="hi")])
    assert len(result.generations) == 1
    assert result.generations[0].message.content == '{"score": 0.7}'


@pytest.mark.asyncio
async def test_chat_model_real_call_passes_translated_messages(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(
        "evalgate.evaluator.rag.ragas_adapter.litellm.acompletion",
        AsyncMock(side_effect=fake_acompletion),
    )

    chat = LiteLLMChatModel(model="ollama/qwen2.5:7b", params={"temperature": 0.0})
    result = await chat._agenerate(
        [SystemMessage(content="be terse"), HumanMessage(content="ping")]
    )

    assert captured["model"] == "ollama/qwen2.5:7b"
    assert captured["temperature"] == 0.0
    assert captured["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "ping"},
    ]
    assert result.generations[0].message.content == "ok"


@pytest.mark.asyncio
async def test_chat_model_swallows_litellm_errors_into_payload(monkeypatch):
    async def boom(**_: Any) -> dict[str, Any]:
        raise RuntimeError("network gone")

    monkeypatch.setattr(
        "evalgate.evaluator.rag.ragas_adapter.litellm.acompletion",
        AsyncMock(side_effect=boom),
    )
    chat = LiteLLMChatModel(model="ollama/qwen2.5:7b")
    result = await chat._agenerate([HumanMessage(content="x")])
    assert "litellm-call-failed" in result.generations[0].message.content


def test_embeddings_mock_mode_is_deterministic_and_dimensioned():
    emb = LiteLLMEmbeddings("ollama/qwen3-embedding:8b", mock_mode=True, dim=128)
    a = emb.embed_query("billing question")
    b = emb.embed_query("billing question")
    c = emb.embed_query("different topic")
    assert a == b
    assert a != c
    assert len(a) == 128
    assert all(-1.0 <= x <= 1.0 for x in a)


@pytest.mark.asyncio
async def test_embeddings_real_call_extracts_vectors(monkeypatch):
    async def fake_aembedding(model: str, input: list[str]) -> dict[str, Any]:
        return {"data": [{"embedding": [0.1, 0.2, 0.3]} for _ in input]}

    monkeypatch.setattr(
        "evalgate.evaluator.rag.ragas_adapter.litellm.aembedding",
        AsyncMock(side_effect=fake_aembedding),
    )
    emb = LiteLLMEmbeddings("ollama/qwen3-embedding:8b", mock_mode=False)
    out = await emb.aembed_documents(["a", "b"])
    assert out == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
