"""PII detector backed by ``presidio-analyzer`` regex/pattern recognizers.

We intentionally **bypass Presidio's default ``AnalyzerEngine``** to avoid
its dependency on a spaCy language model: every recognizer in our default
allow-list is a :class:`PatternRecognizer` (regex-based), so we can call
each recognizer's ``analyze()`` directly with no NLP artifacts. This keeps
``EVALGATE_MOCK_LLM=1`` runs and CI fully offline — the only install-time
cost is the ``presidio-analyzer`` wheel.

If a non-pattern recognizer is requested (e.g. ``PERSON`` which needs NER)
we silently skip it: this is the well-defined fallback documented in
:mod:`evalgate.safety` and :mod:`docs/PHASE_10_PLAN`.
"""

from __future__ import annotations

import logging
from threading import Lock
from typing import Any

from evalgate.judge.prompt_spec import PiiDetectorSpec
from evalgate.safety.detector import PiiHit, PiiScanResult

_logger = logging.getLogger(__name__)


class PresidioPiiDetector:
    """Lazily-initialised Presidio detector. Thread-safe singleton-like init.

    Construction is cheap: it only stores the spec. The first call to
    :meth:`scan` builds the recognizers from
    ``presidio_analyzer.predefined_recognizers``; subsequent calls reuse them.
    """

    def __init__(self, spec: PiiDetectorSpec):
        self._spec = spec
        self._recognizers: list[Any] | None = None
        self._lock = Lock()

    def _ensure_loaded(self) -> list[Any]:
        if self._recognizers is not None:
            return self._recognizers
        with self._lock:
            if self._recognizers is not None:
                return self._recognizers
            self._recognizers = _build_pattern_recognizers(self._spec.entities)
            return self._recognizers

    def warmup(self) -> None:
        """Eagerly build recognizers.

        Called at pipeline-build time so a missing/broken ``presidio-analyzer``
        install surfaces as a loud RuntimeError at run start, instead of being
        swallowed per-case in :meth:`scan` and silently reporting 0% PII (a
        false "clean" safety signal) for the entire run.
        """
        self._ensure_loaded()

    def scan(self, text: str) -> PiiScanResult:
        """Run every configured recognizer over ``text`` and collect hits.

        Returns an empty result for empty / non-string input so callers don't
        need to guard. Failures inside a single recognizer are logged at
        ``debug`` and skipped — never propagated.
        """
        if not isinstance(text, str) or not text:
            return PiiScanResult()
        recognizers = self._ensure_loaded()
        hits: list[PiiHit] = []
        for recognizer in recognizers:
            try:
                results = recognizer.analyze(
                    text=text,
                    entities=self._spec.entities,
                    nlp_artifacts=None,
                )
            except Exception:
                _logger.debug("recognizer %r failed on text", recognizer, exc_info=True)
                continue
            for r in results or []:
                if float(r.score) < self._spec.score_threshold:
                    continue
                hits.append(
                    PiiHit(
                        entity_type=str(r.entity_type),
                        start=int(r.start),
                        end=int(r.end),
                        score=float(r.score),
                    )
                )
        return PiiScanResult(hits=hits)


# Map presidio entity type → predefined recognizer class. Keep this list
# explicit (rather than `predefined_recognizers.predefined_recognizers`) so
# we never accidentally load an NLP-dependent recognizer.
_RECOGNIZER_CLASS_BY_ENTITY: dict[str, str] = {
    "EMAIL_ADDRESS": "EmailRecognizer",
    "PHONE_NUMBER": "PhoneRecognizer",
    "US_SSN": "UsSsnRecognizer",
    "CREDIT_CARD": "CreditCardRecognizer",
    "IP_ADDRESS": "IpRecognizer",
    "URL": "UrlRecognizer",
    "IBAN_CODE": "IbanRecognizer",
    "US_BANK_NUMBER": "UsBankRecognizer",
    "US_ITIN": "UsItinRecognizer",
    "US_PASSPORT": "UsPassportRecognizer",
    "US_DRIVER_LICENSE": "UsLicenseRecognizer",
    "MEDICAL_LICENSE": "MedicalLicenseRecognizer",
    "CRYPTO": "CryptoRecognizer",
}


def _build_pattern_recognizers(entities: list[str]) -> list[Any]:
    """Instantiate just the regex/pattern-based predefined recognizers.

    Lazy-imports presidio so module import doesn't depend on the package.
    Unknown entity types are logged and skipped.
    """
    try:
        from presidio_analyzer import predefined_recognizers
    except ImportError as exc:  # pragma: no cover — surfaced via PHASE_10_PLAN
        raise RuntimeError(
            "presidio-analyzer is required for safety.pii detection; "
            "install it via `uv sync` or set safety.enabled=false in prompt.yaml"
        ) from exc

    out: list[Any] = []
    for entity in entities:
        cls_name = _RECOGNIZER_CLASS_BY_ENTITY.get(entity)
        if cls_name is None:
            _logger.debug("no pattern recognizer mapped for entity %s; skipping", entity)
            continue
        cls = getattr(predefined_recognizers, cls_name, None)
        if cls is None:  # pragma: no cover — presidio version drift
            _logger.debug("presidio missing recognizer class %s; skipping", cls_name)
            continue
        try:
            out.append(cls())
        except Exception:  # pragma: no cover — recognizer init drift
            _logger.debug("recognizer %s failed to instantiate", cls_name, exc_info=True)
            continue
    return out
