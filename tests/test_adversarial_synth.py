"""AdversarialSynth.synthesize: templates, k, mock determinism, JSON tolerance."""

from __future__ import annotations

import evalgate.adversarial.synth as synth
from evalgate.adversarial.synth import (
    ADVERSARIAL_TEMPLATES,
    GeneratedCase,
    synthesize,
)

_TEMPLATE_KEYS = {t.key for t in ADVERSARIAL_TEMPLATES}


async def test_mock_is_deterministic_and_spread_across_templates():
    a = await synthesize(tag="billing", k=8, mock=True)
    b = await synthesize(tag="billing", k=8, mock=True)

    assert len(a) == 8
    # Same inputs twice -> identical outputs (no LLM, no randomness).
    assert [c.input for c in a] == [c.input for c in b]
    # First 4 cycle the full template palette.
    assert [c.template for c in a[:4]] == [t.key for t in ADVERSARIAL_TEMPLATES]
    for c in a:
        assert c.template in _TEMPLATE_KEYS
        assert c.tags[0] == "billing"
        assert "adversarial" in c.tags
        assert c.template in c.tags


async def test_mock_mirrors_exemplar_input_key():
    exemplars = [{"input": {"prompt": "How do refunds work?"}}]
    cases = await synthesize(tag="refunds", exemplars=exemplars, k=2, mock=True)
    assert cases
    for c in cases:
        assert set(c.input.keys()) == {"prompt"}


async def test_mock_default_input_key_when_no_exemplars():
    cases = await synthesize(tag="x", k=1, mock=True)
    assert list(cases[0].input.keys()) == [synth.DEFAULT_INPUT_KEY]


async def test_injection_case_contains_jailbreak_phrasing():
    cases = await synthesize(tag="billing", k=4, mock=True)
    injection = [c for c in cases if c.template == "prompt_injection"]
    assert injection
    assert "ignore previous instructions" in injection[0].input["question"].lower()


async def test_k_zero_returns_empty():
    assert await synthesize(tag="t", k=0, mock=True) == []
    assert await synthesize(tag="t", k=-5, mock=True) == []


async def test_real_path_parses_llm_json(monkeypatch):
    payload = (
        '{"cases": ['
        '{"input": {"question": "edge case?"}, "template": "boundary", "rationale": "r1"},'
        '{"input": {"question": "who is it?"}, "template": "ambiguity", "rationale": "r2"}'
        "]}"
    )

    async def fake_call(**_kwargs):
        return payload, {}

    monkeypatch.setattr(synth, "acompletion_json", fake_call)
    cases = await synthesize(tag="billing", k=10, mock=False)
    assert len(cases) == 2
    assert isinstance(cases[0], GeneratedCase)
    assert cases[0].template == "boundary"
    assert cases[1].input == {"question": "who is it?"}
    assert cases[0].tags == ["billing", "adversarial", "boundary"]


async def test_real_path_truncates_to_k(monkeypatch):
    cases_json = ",".join(
        f'{{"input": {{"question": "q{i}"}}, "template": "boundary"}}' for i in range(20)
    )

    async def fake_call(**_kwargs):
        return '{"cases": [' + cases_json + "]}", {}

    monkeypatch.setattr(synth, "acompletion_json", fake_call)
    cases = await synthesize(tag="t", k=5, mock=False)
    assert len(cases) == 5


async def test_real_path_tolerates_bad_json(monkeypatch):
    async def fake_call(**_kwargs):
        return "not json at all", {}

    monkeypatch.setattr(synth, "acompletion_json", fake_call)
    cases = await synthesize(tag="t", k=5, mock=False)
    assert cases == []


async def test_real_path_skips_malformed_cases_and_normalizes_template(monkeypatch):
    payload = (
        '{"cases": ['
        '{"input": {}, "template": "boundary"},'  # empty input -> skipped
        '{"input": "a bare string", "template": "weird"},'  # string -> wrapped; bad template -> boundary
        '{"template": "ambiguity"}'  # missing input -> skipped
        "]}"
    )

    async def fake_call(**_kwargs):
        return payload, {}

    monkeypatch.setattr(synth, "acompletion_json", fake_call)
    cases = await synthesize(tag="t", k=10, mock=False)
    assert len(cases) == 1
    assert cases[0].input == {"question": "a bare string"}
    assert cases[0].template == "boundary"  # unknown template normalized
