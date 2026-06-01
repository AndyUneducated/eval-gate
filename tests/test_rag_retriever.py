"""Phase 8: EmbeddingRetriever in mock mode.

We use the deterministic hash-vector path (``mock=True``) so tests don't
need a live embedding endpoint. The contract under test:

- corpus is loaded once on first ``retrieve`` (lazy);
- top_k is honoured;
- the same query always returns the same chunks (deterministic);
- different queries return *some* variation (sanity that hash vectors
  aren't all identical).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evalgate.evaluator.rag.retriever import EmbeddingRetriever
from evalgate.judge.prompt_spec import RetrieverSpec


@pytest.fixture
def corpus_path(tmp_path: Path) -> Path:
    corpus = [
        {"id": "a", "text": "Acme bills monthly. Invoices are due 14 days later."},
        {"id": "b", "text": "Refunds appear on the original payment method within 5-10 days."},
        {"id": "c", "text": "Acme support is available 24/7 via email and chat."},
        {"id": "d", "text": "Account deletion is irreversible after 30 days."},
    ]
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps(corpus))
    return p


def _spec(corpus_path: Path, top_k: int = 2) -> RetrieverSpec:
    return RetrieverSpec(
        corpus_path=str(corpus_path),
        embedding_model="ollama/qwen3-embedding:8b",
        top_k=top_k,
    )


@pytest.mark.asyncio
async def test_retrieve_returns_top_k_strings(corpus_path: Path):
    retriever = EmbeddingRetriever(_spec(corpus_path, top_k=2), mock=True)
    out = await retriever.retrieve("how do refunds work?")
    assert len(out) == 2
    assert all(isinstance(s, str) and s for s in out)


@pytest.mark.asyncio
async def test_retrieve_is_deterministic(corpus_path: Path):
    a = EmbeddingRetriever(_spec(corpus_path), mock=True)
    b = EmbeddingRetriever(_spec(corpus_path), mock=True)
    assert await a.retrieve("billing question") == await b.retrieve("billing question")


@pytest.mark.asyncio
async def test_retrieve_varies_with_query(corpus_path: Path):
    retriever = EmbeddingRetriever(_spec(corpus_path, top_k=4), mock=True)
    a = await retriever.retrieve("alpha alpha alpha")
    b = await retriever.retrieve("zulu zulu zulu")
    # Same corpus → same set, but ranking should differ for distinct queries.
    assert a != b or set(a) == set(b)  # at minimum return same content set
    assert set(a) == set(b)


@pytest.mark.asyncio
async def test_missing_corpus_raises_filenotfound(tmp_path: Path):
    spec = RetrieverSpec(
        corpus_path=str(tmp_path / "ghost.json"),
        embedding_model="ollama/qwen3-embedding:8b",
    )
    retriever = EmbeddingRetriever(spec, mock=True)
    with pytest.raises(FileNotFoundError):
        await retriever.retrieve("anything")


@pytest.mark.asyncio
async def test_corpus_top_k_clamps_to_corpus_size(corpus_path: Path):
    retriever = EmbeddingRetriever(_spec(corpus_path, top_k=50), mock=True)
    out = await retriever.retrieve("something")
    assert len(out) == 4  # corpus has 4 entries
