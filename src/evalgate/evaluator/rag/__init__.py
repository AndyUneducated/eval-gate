"""RAG-aware evaluator (Phase 8) backed by the official ``ragas`` package.

Public surface intentionally small:

- :class:`RagEvaluator` is what the router instantiates.
- :class:`EmbeddingRetriever` is exported for tests / smoke scripts.
- The ragas adapter (``ragas_adapter``) is private — its job is to
  translate ragas's langchain calls into ``litellm.acompletion`` /
  ``litellm.aembedding`` so we don't add a langchain LLM provider as a
  dependency.
"""

from evalgate.evaluator.rag.evaluator import RagEvaluator
from evalgate.evaluator.rag.retriever import EmbeddingRetriever

__all__ = ["EmbeddingRetriever", "RagEvaluator"]
