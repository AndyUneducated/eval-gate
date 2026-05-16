"""Embedding-based retriever for the RAG candidate.

Phase 8 ships exactly one retriever flavour: take a JSON corpus on
disk, embed every chunk once at construction (cached in memory), and
serve top-K cosine-similar chunks per query.

Why it lives next to the evaluator (not in ``judge/``):
- It's part of the *candidate* path, not the judge path.
- The mock contract (``EVALGATE_MOCK_LLM=1`` → hash vectors) is shared
  with :mod:`ragas_adapter` so a single env var disables every external
  call in the RAG branch.

Corpus format on disk (``corpus_path`` in YAML):

::

    [
      {"id": "chunk-1", "text": "..."},
      {"id": "chunk-2", "text": "..."}
    ]
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from evalgate.evaluator.rag.ragas_adapter import LiteLLMEmbeddings
from evalgate.judge.prompt_spec import RetrieverSpec


class Retriever(Protocol):
    """Anything that maps a query string to top-K context strings."""

    async def retrieve(self, query: str) -> list[str]: ...


class EmbeddingRetriever:
    """Cosine-over-embeddings retriever with one-shot corpus embed.

    The first ``retrieve`` call lazily embeds the entire corpus (so
    constructing the evaluator is cheap and tests that never call
    ``retrieve`` skip the LLM call entirely). Subsequent calls reuse
    the cached matrix.

    ``mock`` follows the rest of the RAG stack: when true, embeddings are
    deterministic SHA-based vectors (no network IO) and the retriever
    still returns a stable top-K ordering — good enough for unit tests
    that just want the contract exercised.
    """

    def __init__(self, spec: RetrieverSpec, *, mock: bool = False):
        self.spec = spec
        self.mock = mock
        self._embeddings = LiteLLMEmbeddings(spec.embedding_model, mock_mode=mock)
        self._corpus: list[dict[str, Any]] | None = None
        self._matrix: np.ndarray | None = None
        self._lock = asyncio.Lock()

    def _load_corpus(self) -> list[dict[str, Any]]:
        path = Path(self.spec.corpus_path).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"retriever corpus not found: {path}")
        raw = json.loads(path.read_text())
        if not isinstance(raw, list) or not raw:
            raise ValueError(f"retriever corpus must be a non-empty list: {path}")
        out: list[dict[str, Any]] = []
        for i, entry in enumerate(raw):
            if not isinstance(entry, dict) or not entry.get("text"):
                raise ValueError(f"corpus[{i}] must be a dict with non-empty 'text' field")
            out.append({"id": str(entry.get("id") or f"chunk-{i}"), "text": str(entry["text"])})
        return out

    async def _ensure_embedded(self) -> None:
        if self._matrix is not None:
            return
        async with self._lock:
            if self._matrix is not None:
                return
            corpus = self._load_corpus()
            vectors = await self._embeddings.aembed_documents([c["text"] for c in corpus])
            self._corpus = corpus
            mat = np.asarray(vectors, dtype=np.float64)
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._matrix = mat / norms

    async def retrieve(self, query: str) -> list[str]:
        await self._ensure_embedded()
        assert self._matrix is not None and self._corpus is not None
        q = await self._embeddings.aembed_query(query)
        qv = np.asarray(q, dtype=np.float64)
        n = float(np.linalg.norm(qv)) or 1.0
        qv = qv / n
        sims = self._matrix @ qv
        k = min(self.spec.top_k, len(self._corpus))
        top_idx = np.argsort(-sims)[:k]
        return [self._corpus[int(i)]["text"] for i in top_idx]


def is_mock_env() -> bool:
    """Single source of truth for ``EVALGATE_MOCK_LLM=1`` semantics, also
    consulted by the RAG evaluator so all three (retriever, candidate,
    ragas judge) flip together."""
    return os.environ.get("EVALGATE_MOCK_LLM", "").lower() in {"1", "true", "yes"}
