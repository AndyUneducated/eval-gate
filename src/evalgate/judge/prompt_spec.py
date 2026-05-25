"""Schema + loader for prompt.yaml (Phase 6, breaking change).

A prompt file bundles **the candidate prompt** (what we want to evaluate) and
**the judge policy** (one or more judges + how to aggregate them).

Phase 6 deliberately drops the Phase 5 single-`judge:` shape — keeping both
would force every wrapper to normalise on the fly. Migration is mechanical
(`judge:` -> `judges: [...]` + `judge_policy:`), and the loader raises a
descriptive error if it sees the legacy shape.

We tolerate missing `{field}` placeholders in `user_template` via
``defaultdict(str)`` — eval cases coming from heterogeneous traces won't all
share the same input keys, and we'd rather render an empty slot than crash.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CandidateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    system: str | None = None
    user_template: str = "{input}"
    params: dict[str, Any] = Field(default_factory=dict)


class JudgeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    # Required for pointwise mode; ignored by PairwiseJudge (which uses a
    # fixed A/B template). Keep it required at schema level so configs stay
    # uniform across modes.
    rubric: str
    params: dict[str, Any] = Field(default_factory=dict)


JudgeMode = Literal["pointwise", "pairwise"]


class JudgePolicySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: JudgeMode
    k: int = Field(default=1, ge=1, le=11)
    position_swap: bool = True
    concurrency: int = Field(default=4, ge=1, le=32)


RagMetricName = Literal["faithfulness", "context_precision", "answer_relevance"]


class RetrieverSpec(BaseModel):
    """Phase 8: how the candidate retrieves contexts at run time.

    Currently we ship one ``kind`` (``embedding``) — a deterministic
    embedding-then-cosine retriever over a JSON corpus file. The kind tag
    is left here so future Phase iterations can plug in BM25 / a real
    vector DB without touching call sites.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["embedding"] = "embedding"
    corpus_path: str
    embedding_model: str
    top_k: int = Field(default=4, ge=1, le=50)


class RagEvaluatorSpec(BaseModel):
    """Phase 8: ragas knobs (which metrics, which judge LLM, which embeddings).

    The judge LLM here is the model ragas itself uses to e.g. extract
    claims for ``faithfulness`` — it's independent of the candidate
    generator's model and of the embedding model used for retrieval.
    """

    model_config = ConfigDict(extra="forbid")

    llm_model: str
    embedding_model: str
    metrics: list[RagMetricName] = Field(
        default_factory=lambda: ["faithfulness", "context_precision", "answer_relevance"],
        min_length=1,
    )


class PiiDetectorSpec(BaseModel):
    """Phase 10: Presidio-driven PII detector knobs.

    ``entities`` is the allow-list of Presidio recognizer types we run. The
    defaults are all regex/pattern-based (no spaCy NER required), so the
    pipeline works in pure-CI without downloading a language model.
    """

    model_config = ConfigDict(extra="forbid")

    entities: list[str] = Field(
        default_factory=lambda: [
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "US_SSN",
            "CREDIT_CARD",
            "IP_ADDRESS",
            "URL",
            "IBAN_CODE",
        ],
        min_length=1,
    )
    # 0.4 catches Presidio's low-confidence phone matches (which it scores at
    # exactly 0.4 for stand-alone digit groups). Bump per-prompt to 0.5+ to
    # tighten precision; drop to 0.3 to also include weak URL/IP variants.
    score_threshold: float = Field(default=0.4, ge=0.0, le=1.0)


class JailbreakDetectorSpec(BaseModel):
    """Phase 10: keyword + optional LLM-classifier knobs.

    - ``keywords``: when ``None``, the bundled default list is used. Set to a
      custom list to override; pass ``[]`` to disable the keyword path.
    - ``classifier_model``: when ``None``, only the keyword path runs and
      compliance falls back to a refusal heuristic. When set (and not in
      ``EVALGATE_MOCK_LLM=1``), every output where an attempt fired is
      classified by a tiny LiteLLM call.
    """

    model_config = ConfigDict(extra="forbid")

    keywords: list[str] | None = None
    classifier_model: str | None = "ollama/qwen3.5:9b"


class SafetySpec(BaseModel):
    """Phase 10: top-level safety scoring config.

    Attached to every ``PromptSpec`` (default-on). The runner builds a
    :class:`SafetyPipeline` once per run from this block and merges
    ``axis_breakdown["safety"]`` into every outcome before persistence.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    pii: PiiDetectorSpec = Field(default_factory=PiiDetectorSpec)
    jailbreak: JailbreakDetectorSpec = Field(default_factory=JailbreakDetectorSpec)


class AgentRuntimeSpec(BaseModel):
    """Phase 9: planner/tool runtime knobs for `task_type=agent`.

    The runtime drives a strict JSON action loop:
    - {"action":"call_tool","tool":"...","args":{...}}
    - {"action":"final_answer","answer":"..."}
    """

    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(default=6, ge=1, le=32)
    tool_names: list[str] = Field(min_length=1)
    planner_model: str | None = None


class PromptSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    candidate: CandidateSpec
    judges: list[JudgeSpec] = Field(min_length=1)
    judge_policy: JudgePolicySpec
    # Phase 8 RAG: optional. When the eval set contains any ``task_type=rag``
    # case, both must be set; the EvaluatorRouter raises at dispatch time
    # otherwise. Generic-only prompts leave both ``None``.
    retriever: RetrieverSpec | None = None
    rag_evaluator: RagEvaluatorSpec | None = None
    # Phase 9 Agent runtime config. If omitted, `task_type=agent` cases
    # remain unsupported and runner emits per-case unsupported_task_type
    # records (same behavior as missing rag blocks for task_type=rag).
    agent_runtime: AgentRuntimeSpec | None = None
    # Phase 10 Safety config. Default-on with default detectors; set
    # ``safety.enabled=false`` to skip the pipeline entirely.
    safety: SafetySpec = Field(default_factory=SafetySpec)

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_judge_singular(cls, data: Any) -> Any:
        if isinstance(data, dict) and "judge" in data:
            raise ValueError(
                "prompt.yaml: the singular `judge:` key was removed in Phase 6. "
                "Use a list under `judges:` and add a `judge_policy:` block. "
                "Minimal example:\n"
                "  judges:\n"
                '    - {model: ollama/qwen3.5:9b, rubric: "..."}\n'
                "  judge_policy: {mode: pointwise, k: 1}"
            )
        return data

    @model_validator(mode="after")
    def _rag_blocks_paired(self) -> PromptSpec:
        if (self.retriever is None) != (self.rag_evaluator is None):
            raise ValueError(
                "prompt.yaml: `retriever:` and `rag_evaluator:` must be set "
                "together (or both omitted). RAG cases need both; generic-only "
                "prompts can omit both."
            )
        return self

    @model_validator(mode="after")
    def _validate_agent_runtime(self) -> PromptSpec:
        if self.agent_runtime is None:
            return self
        cleaned = [t.strip() for t in self.agent_runtime.tool_names if t and t.strip()]
        if not cleaned:
            raise ValueError("prompt.yaml: agent_runtime.tool_names must contain at least one tool")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("prompt.yaml: agent_runtime.tool_names contains duplicates")
        self.agent_runtime = self.agent_runtime.model_copy(update={"tool_names": cleaned})
        return self

    def render_messages(self, case_input: dict[str, Any]) -> list[dict[str, str]]:
        """Render `candidate.user_template` against a case's `input` dict.

        Missing fields render as the empty string so heterogeneous eval sets
        don't crash a run.
        """
        safe = defaultdict(str, {k: _stringify(v) for k, v in case_input.items()})
        if "input" not in safe:
            safe["input"] = _stringify(case_input)
        user_text = self.candidate.user_template.format_map(safe)
        messages: list[dict[str, str]] = []
        if self.candidate.system:
            messages.append({"role": "system", "content": self.candidate.system})
        messages.append({"role": "user", "content": user_text})
        return messages


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        import json

        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def load_prompt_spec(path: str | Path) -> PromptSpec:
    """Load + validate a prompt.yaml. Returns `PromptSpec` ready for the runner."""
    p = Path(path)
    raw = p.read_bytes()
    data = yaml.safe_load(raw) or {}
    return PromptSpec.model_validate(data)


def hash_prompt(path: str | Path) -> str:
    """sha256 of the raw YAML bytes — used for `eval_runs.prompt_hash` audit."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
