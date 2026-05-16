"""prompt.yaml schema + render contract (Phase 6 schema).

The render pipeline must tolerate cases whose `input` dicts lack the
placeholders mentioned in `user_template` — eval sets are heterogeneous and
we explicitly do not want a render-time KeyError to crash a whole run.

Phase 6 made the schema breaking: `judge:` singular is gone, you must give
a `judges: [...]` list and a `judge_policy:` block.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from evalgate.judge.prompt_spec import (
    PromptSpec,
    hash_prompt,
    load_prompt_spec,
)

_YAML = """
name: t
candidate:
  model: ollama/qwen2.5:7b
  system: "be helpful"
  user_template: "Q: {prompt}"
  params: {temperature: 0.0}
judges:
  - model: ollama/qwen2.5:7b
    rubric: "rate 0..1 json"
    params: {}
judge_policy:
  mode: pointwise
  k: 1
  position_swap: false
  concurrency: 4
"""


def _write(tmp_path, body: str = _YAML):
    p = tmp_path / "p.yaml"
    p.write_text(body)
    return p


def test_load_prompt_spec_happy_path(tmp_path):
    spec = load_prompt_spec(_write(tmp_path))
    assert isinstance(spec, PromptSpec)
    assert spec.candidate.model == "ollama/qwen2.5:7b"
    assert len(spec.judges) == 1
    assert spec.judges[0].rubric.startswith("rate")
    assert spec.judge_policy.mode == "pointwise"


def test_render_substitutes_known_keys(tmp_path):
    spec = load_prompt_spec(_write(tmp_path))
    msgs = spec.render_messages({"prompt": "what is 2+2?"})
    assert msgs[0]["role"] == "system"
    assert msgs[1]["role"] == "user"
    assert msgs[1]["content"] == "Q: what is 2+2?"


def test_render_missing_key_is_empty_string(tmp_path):
    spec = load_prompt_spec(_write(tmp_path))
    msgs = spec.render_messages({"other": "x"})
    assert msgs[-1]["content"] == "Q: "


def test_render_serialises_dict_values(tmp_path):
    spec = load_prompt_spec(_write(tmp_path))
    msgs = spec.render_messages({"prompt": {"a": 1}})
    assert '"a": 1' in msgs[-1]["content"]


def test_hash_prompt_is_stable(tmp_path):
    p = _write(tmp_path)
    h1 = hash_prompt(p)
    h2 = hash_prompt(p)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_load_rejects_unknown_keys(tmp_path):
    bad = _write(tmp_path, _YAML + "\nextra: forbidden\n")
    with pytest.raises(ValidationError):
        load_prompt_spec(bad)


def test_legacy_singular_judge_rejected(tmp_path):
    """Phase 5 used `judge:` (singular). Phase 6 rejects it with a hint."""
    legacy = """
name: t
candidate:
  model: ollama/qwen2.5:7b
  user_template: "{prompt}"
judge:
  model: ollama/qwen2.5:7b
  rubric: "rate 0..1 json"
judge_policy:
  mode: pointwise
"""
    bad = _write(tmp_path, legacy)
    with pytest.raises(ValidationError) as exc:
        load_prompt_spec(bad)
    assert "judges" in str(exc.value).lower() or "judge_policy" in str(exc.value)


def test_judges_must_be_nonempty(tmp_path):
    body = """
name: t
candidate:
  model: ollama/qwen2.5:7b
  user_template: "{prompt}"
judges: []
judge_policy:
  mode: pointwise
"""
    with pytest.raises(ValidationError):
        load_prompt_spec(_write(tmp_path, body))


def test_judge_policy_mode_required(tmp_path):
    body = """
name: t
candidate:
  model: ollama/qwen2.5:7b
  user_template: "{prompt}"
judges:
  - model: ollama/qwen2.5:7b
    rubric: "rate"
judge_policy: {}
"""
    with pytest.raises(ValidationError):
        load_prompt_spec(_write(tmp_path, body))


def test_agent_runtime_requires_nonempty_tool_names(tmp_path):
    body = (
        _YAML
        + """
agent_runtime:
  max_steps: 4
  tool_names: []
"""
    )
    with pytest.raises(ValidationError):
        load_prompt_spec(_write(tmp_path, body))


def test_agent_runtime_rejects_duplicate_tool_names(tmp_path):
    body = (
        _YAML
        + """
agent_runtime:
  max_steps: 4
  tool_names:
    - lookup_invoice
    - lookup_invoice
"""
    )
    with pytest.raises(ValidationError):
        load_prompt_spec(_write(tmp_path, body))


def test_agent_runtime_accepts_planner_model_override(tmp_path):
    body = (
        _YAML
        + """
agent_runtime:
  max_steps: 4
  planner_model: ollama/qwen2.5:7b
  tool_names:
    - lookup_invoice
    - fetch_policy
"""
    )
    spec = load_prompt_spec(_write(tmp_path, body))
    assert spec.agent_runtime is not None
    assert spec.agent_runtime.planner_model == "ollama/qwen2.5:7b"
    assert spec.agent_runtime.tool_names == ["lookup_invoice", "fetch_policy"]
