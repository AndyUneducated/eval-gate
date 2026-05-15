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


class PromptSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    candidate: CandidateSpec
    judges: list[JudgeSpec] = Field(min_length=1)
    judge_policy: JudgePolicySpec

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_judge_singular(cls, data: Any) -> Any:
        if isinstance(data, dict) and "judge" in data:
            raise ValueError(
                "prompt.yaml: the singular `judge:` key was removed in Phase 6. "
                "Use a list under `judges:` and add a `judge_policy:` block. "
                "Minimal example:\n"
                "  judges:\n"
                '    - {model: ollama/qwen2.5:7b, rubric: "..."}\n'
                "  judge_policy: {mode: pointwise, k: 1}"
            )
        return data

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
