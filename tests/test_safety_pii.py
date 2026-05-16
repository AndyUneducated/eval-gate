"""Phase 10: Presidio PII detector behaviour with hand-rolled fixtures.

We directly drive :class:`PresidioPiiDetector` (no AnalyzerEngine), so the
test is offline and deterministic. Goal: validate precision-bearing
positives + a couple of obvious near-miss negatives + the
``score_threshold`` knob.
"""

from __future__ import annotations

import pytest

from evalgate.judge.prompt_spec import PiiDetectorSpec
from evalgate.safety.pii import PresidioPiiDetector


def _detector(**overrides) -> PresidioPiiDetector:
    spec = PiiDetectorSpec(**overrides)
    return PresidioPiiDetector(spec)


def _entity_types(result) -> set[str]:
    return {h.entity_type for h in result.hits}


def test_email_is_detected_with_high_confidence():
    det = _detector()
    result = det.scan("Please email alice@example.com for the report.")
    assert "EMAIL_ADDRESS" in _entity_types(result)
    assert result.violation


def test_phone_number_is_detected_at_default_threshold():
    det = _detector()
    result = det.scan("Call me at +1 (415) 555-1212 anytime.")
    # default threshold 0.4 catches presidio's low-confidence phone matches
    assert "PHONE_NUMBER" in _entity_types(result)


def test_strict_threshold_drops_weak_phone_matches():
    det = _detector(score_threshold=0.85)
    result = det.scan("Call me at 415-555-1212.")
    # Presidio scores stand-alone phone digit groups around 0.4; with
    # threshold 0.85 we expect this to fall out of the result.
    assert "PHONE_NUMBER" not in _entity_types(result)


def test_us_ssn_with_valid_format_is_detected():
    det = _detector()
    result = det.scan("Employee SSN: 525-12-3456 on file.")
    assert "US_SSN" in _entity_types(result)


def test_credit_card_with_test_number_is_detected():
    det = _detector()
    result = det.scan("Card on file: 4111-1111-1111-1111.")
    assert "CREDIT_CARD" in _entity_types(result)


def test_ip_and_url_are_detected():
    det = _detector()
    result = det.scan("Visit https://evil.example.com/x or 10.0.0.5 for details.")
    types = _entity_types(result)
    assert "URL" in types
    assert "IP_ADDRESS" in types


def test_clean_text_is_not_flagged():
    det = _detector()
    result = det.scan("This is a perfectly mundane refund question with no PII.")
    assert not result.violation
    assert result.hits == []


def test_empty_or_non_string_input_returns_empty_result():
    det = _detector()
    assert det.scan("").hits == []
    assert det.scan(None).hits == []  # type: ignore[arg-type]
    assert det.scan(123).hits == []  # type: ignore[arg-type]


def test_unknown_entity_in_allowlist_is_silently_skipped():
    """Phase 10: unknown entity types (e.g. ``PERSON`` which needs NER)
    don't crash detector init — they're silently dropped from the
    pattern-recognizer allow-list."""

    det = _detector(entities=["PERSON", "EMAIL_ADDRESS"])
    result = det.scan("Reach out to alice@example.com for follow-up.")
    # ``PERSON`` is dropped; ``EMAIL_ADDRESS`` still fires.
    assert "EMAIL_ADDRESS" in _entity_types(result)


@pytest.mark.parametrize(
    "text",
    [
        # Random 9-digit mass — not formatted as SSN, no separators.
        "Order tracking number 123456789 received.",
        # Free text with no recognizable pattern.
        "This is just a refund question.",
    ],
)
def test_obvious_negatives_dont_flag(text):
    det = _detector()
    result = det.scan(text)
    assert not result.violation
