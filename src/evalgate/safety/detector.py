"""Shared types for the safety package.

We intentionally don't make the detectors a Protocol here — they have
detector-specific signatures (PII reports a list of entity hits; jailbreak
reports keyword + compliance booleans). Instead we expose lean per-detector
result objects that the :class:`SafetyPipeline` reduces to four
gate-ready sub-metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PiiHit:
    """One Presidio recognizer match against a slice of text."""

    entity_type: str
    start: int
    end: int
    score: float


@dataclass
class PiiScanResult:
    """All PII hits found in a single piece of text."""

    hits: list[PiiHit] = field(default_factory=list)

    @property
    def violation(self) -> bool:
        return bool(self.hits)


@dataclass
class JailbreakInputResult:
    """Whether the input looks like a jailbreak attempt."""

    attempt: bool
    matched_keywords: list[str] = field(default_factory=list)


@dataclass
class JailbreakOutputResult:
    """Whether the candidate output complied with a jailbreak attempt.

    ``compliance`` is meaningful only when an ``attempt`` fired upstream;
    otherwise it stays ``False`` regardless of the output. ``raw`` carries
    the classifier's JSON payload for audit.
    """

    compliance: bool
    classifier_used: bool = False
    raw: dict[str, Any] | None = None


@dataclass
class SafetyResult:
    """Reduced per-case result that the runner merges into the outcome.

    Each rate is 0.0 or 1.0 per case (mean across cases = rate).
    """

    pii_input_rate: float = 0.0
    pii_output_leak_rate: float = 0.0
    jailbreak_attempt_rate: float = 0.0
    jailbreak_compliance_rate: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def as_axis_metrics(self) -> dict[str, float]:
        return {
            "pii_input_rate": float(self.pii_input_rate),
            "pii_output_leak_rate": float(self.pii_output_leak_rate),
            "jailbreak_attempt_rate": float(self.jailbreak_attempt_rate),
            "jailbreak_compliance_rate": float(self.jailbreak_compliance_rate),
        }
