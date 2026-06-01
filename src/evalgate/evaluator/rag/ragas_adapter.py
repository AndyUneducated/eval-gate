"""LiteLLM ↔ langchain shim so ragas can drive our existing LLM call layer.

Why this exists:

- ``ragas`` accepts a ``langchain_core.language_models.BaseChatModel`` or
  one of its built-in ``LangchainLLMWrapper`` flavours. We don't want to
  pull in ``langchain-openai`` / ``langchain-anthropic`` per provider —
  every model already speaks ``litellm`` for us.
- Same story for embeddings: ragas wants a
  ``langchain_core.embeddings.Embeddings``; we already wrap embeddings
  via ``litellm.aembedding``.

So we implement the two minimum interfaces ragas exercises and route
them straight into ``litellm.acompletion`` / ``litellm.aembedding``.

If a future ragas release widens the BaseChatModel surface it touches,
we'd have to add stubs here — that's intentional: keep adapter visible.

Ragas itself is loaded via :func:`build_ragas_components`, which is the
only entry point from :mod:`evaluator.rag.evaluator`.
"""

from __future__ import annotations

import hashlib
from typing import Any

import litellm
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from evalgate.judge.prompt_spec import RagEvaluatorSpec
from evalgate.judge.protocol import extract_text, thinking_off_kwargs


def _to_litellm_messages(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """Translate langchain messages → LiteLLM/OpenAI chat format."""
    type_to_role = {
        "system": "system",
        "human": "user",
        "ai": "assistant",
        "tool": "tool",
        "function": "function",
    }
    out: list[dict[str, str]] = []
    for m in messages:
        role = type_to_role.get(m.type, "user")
        content = m.content if isinstance(m.content, str) else str(m.content)
        out.append({"role": role, "content": content})
    return out


class LiteLLMChatModel(BaseChatModel):
    """Minimal langchain ``BaseChatModel`` backed by ``litellm.acompletion``.

    We implement both sync ``_generate`` (langchain calls it from sync
    contexts) and async ``_agenerate`` (used by ragas's async runner).

    ``mock_text``: when set, every call short-circuits to a single
    completion of that string — used by tests so the entire RAG
    evaluator stack runs without network IO. We don't go through
    LiteLLM's ``mock_response`` here because that requires a real call
    construction; for an adapter under test, returning early is cleaner.
    """

    model: str
    params: dict[str, Any] = Field(default_factory=dict)
    mock_text: str | None = None

    @property
    def _llm_type(self) -> str:
        return "litellm-chat"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        # NB: do NOT bounce through ``asyncio.run(self._agenerate(...))``.
        # ragas's Executor schedules each metric as a coroutine and from
        # inside that coroutine calls langchain's sync interface, so any
        # ``asyncio.run`` here re-enters the running loop and explodes
        # with ``RuntimeError: asyncio.run() cannot be called from a
        # running event loop``. LiteLLM ships a sync ``completion`` API —
        # use it directly so the sync path stays sync.
        if self.mock_text is not None:
            return _wrap_text(self.mock_text)

        call_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": _to_litellm_messages(messages),
            **dict(self.params or {}),
        }
        for key, value in thinking_off_kwargs(self.model).items():
            call_kwargs.setdefault(key, value)
        if stop:
            call_kwargs["stop"] = stop
        try:
            resp = litellm.completion(**call_kwargs)
        except Exception as exc:  # surfaced as ragas-side metric failure
            return _wrap_text(f'{{"error": "litellm-call-failed: {exc}"}}')
        return _wrap_text(extract_text(resp))

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.mock_text is not None:
            return _wrap_text(self.mock_text)

        call_kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": _to_litellm_messages(messages),
            **dict(self.params or {}),
        }
        for key, value in thinking_off_kwargs(self.model).items():
            call_kwargs.setdefault(key, value)
        if stop:
            call_kwargs["stop"] = stop
        try:
            resp = await litellm.acompletion(**call_kwargs)
        except Exception as exc:  # surfaced as ragas-side metric failure
            return _wrap_text(f'{{"error": "litellm-call-failed: {exc}"}}')
        text = extract_text(resp)
        return _wrap_text(text)


class LiteLLMEmbeddings(Embeddings):
    """Minimal langchain ``Embeddings`` backed by ``litellm.aembedding``.

    ragas only uses this for ``answer_relevance`` (paraphrase-and-cosine).
    ``mock_mode=True`` returns deterministic hash-based vectors so unit
    tests don't need a real embedding endpoint; the vectors are 384-dim
    so ragas's downstream cosine math has enough range.
    """

    def __init__(
        self,
        model: str,
        *,
        mock_mode: bool = False,
        dim: int = 384,
    ):
        self.model = model
        self.mock_mode = mock_mode
        self.dim = dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.mock_mode:
            return [_hash_vector(t, self.dim) for t in texts]
        resp = await litellm.aembedding(model=self.model, input=texts)
        return [_extract_embedding(item) for item in resp["data"]]

    async def aembed_query(self, text: str) -> list[float]:
        out = await self.aembed_documents([text])
        return out[0]

    def _embed_one(self, text: str) -> list[float]:
        # Same reentry trap as ``LiteLLMChatModel._generate``: ragas
        # invokes the sync ``embed_query`` from inside its async
        # Executor, so ``asyncio.run`` here re-enters the running loop.
        # Route the sync path through LiteLLM's sync ``embedding`` API.
        if self.mock_mode:
            return _hash_vector(text, self.dim)
        resp = litellm.embedding(model=self.model, input=[text])
        return _extract_embedding(resp["data"][0])


def _wrap_text(text: str) -> ChatResult:
    return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


def _extract_embedding(item: Any) -> list[float]:
    if isinstance(item, dict):
        emb = item.get("embedding") or item.get("vector")
    else:
        emb = getattr(item, "embedding", None) or getattr(item, "vector", None)
    if emb is None:
        raise ValueError(f"litellm embedding response item missing 'embedding': {item!r}")
    return [float(x) for x in emb]


def _hash_vector(text: str, dim: int) -> list[float]:
    """Deterministic pseudo-embedding: stable across runs, varies with input.

    Uses SHA-256 expanded to ``dim`` floats in [-1, 1]. Not semantically
    meaningful — only used by mock_mode so ragas's cosine math has *some*
    signal to chew on (different strings → different vectors).
    """
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    out: list[float] = []
    i = 0
    while len(out) < dim:
        # 4 bytes -> uint32 -> centered into [-1, 1].
        chunk = seed[(i * 4) % len(seed) : (i * 4) % len(seed) + 4]
        if len(chunk) < 4:
            chunk = (chunk + seed)[:4]
        val = int.from_bytes(chunk, "big") / (2**32 - 1)
        out.append(val * 2.0 - 1.0)
        i += 1
    return out


def build_ragas_components(
    spec: RagEvaluatorSpec,
    *,
    mock: bool = False,
) -> tuple[Any, Any]:
    """Return ``(LangchainLLMWrapper, LangchainEmbeddingsWrapper)`` ready
    to hand to ragas metrics.

    Importing ragas here (not at module top) keeps generic-only runs from
    paying the import cost. Failures in this branch surface as a clean
    ``RuntimeError`` the runner converts into a per-case error outcome.
    """
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper

    chat_model = LiteLLMChatModel(
        model=spec.llm_model,
        mock_text='{"score": 0.8, "reason": "mock"}' if mock else None,
    )
    embeddings = LiteLLMEmbeddings(model=spec.embedding_model, mock_mode=mock)
    return LangchainLLMWrapper(chat_model), LangchainEmbeddingsWrapper(embeddings)
