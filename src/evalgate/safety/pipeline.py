"""Phase 10 safety orchestrator.

The runner builds one :class:`SafetyPipeline` per run via
:func:`build_safety_pipeline` and calls ``await pipeline.augment(case, outcome)``
right after the per-task evaluator returns. Augmenting is non-destructive:
- merges ``axis_breakdown["safety"]`` into the existing breakdown;
- ORs the derived violation flag into ``outcome.safety_violation`` so legacy
  consumers (badcase finder, the gate's main safety axis) keep working.

The pipeline never raises — any exception inside a detector is caught and
reduced to a zero rate for that detector. We'd rather under-report than break
a run.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from evalgate.judge.prompt_spec import PromptSpec, SafetySpec
from evalgate.safety.detector import SafetyResult
from evalgate.safety.jailbreak import JailbreakDetector
from evalgate.safety.pii import PresidioPiiDetector

if TYPE_CHECKING:
    from evalgate.db.models import EvalCaseRow
    from evalgate.evaluator.base import EvaluationOutcome

_logger = logging.getLogger(__name__)

SAFETY_AXIS = "safety"


class SafetyPipeline:
    """Two detectors, one orchestrator. Stateless across cases."""

    def __init__(
        self,
        *,
        pii: PresidioPiiDetector,
        jailbreak: JailbreakDetector,
    ):
        self._pii = pii
        self._jailbreak = jailbreak

    async def evaluate(
        self,
        *,
        input_text: str,
        output_text: str,
        mock: bool,
    ) -> SafetyResult:
        """Run both detectors. Returns a per-case :class:`SafetyResult`.

        Detector failures are logged at ``debug`` and reduced to ``0.0`` —
        we never raise out of this method.
        """
        details: dict[str, Any] = {}

        try:
            pii_input = self._pii.scan(input_text)
        except Exception:
            _logger.debug("pii.scan(input) failed", exc_info=True)
            pii_input = None
        try:
            pii_output = self._pii.scan(output_text)
        except Exception:
            _logger.debug("pii.scan(output) failed", exc_info=True)
            pii_output = None

        try:
            jb_attempt = self._jailbreak.scan_input(input_text)
        except Exception:
            _logger.debug("jailbreak.scan_input failed", exc_info=True)
            jb_attempt = None

        if jb_attempt is not None:
            try:
                jb_compliance = await self._jailbreak.classify_compliance(
                    input_text=input_text,
                    output_text=output_text,
                    attempt=jb_attempt,
                    mock=mock,
                )
            except Exception:
                _logger.debug("jailbreak.classify_compliance failed", exc_info=True)
                jb_compliance = None
        else:
            jb_compliance = None

        if pii_input is not None:
            details["pii_input"] = [
                {"entity": h.entity_type, "score": h.score} for h in pii_input.hits
            ]
        if pii_output is not None:
            details["pii_output"] = [
                {"entity": h.entity_type, "score": h.score} for h in pii_output.hits
            ]
        if jb_attempt is not None and jb_attempt.attempt:
            details["jailbreak_keywords"] = jb_attempt.matched_keywords
        if jb_compliance is not None and jb_compliance.raw is not None:
            details["jailbreak_classifier"] = jb_compliance.raw

        return SafetyResult(
            pii_input_rate=1.0 if pii_input and pii_input.violation else 0.0,
            pii_output_leak_rate=1.0 if pii_output and pii_output.violation else 0.0,
            jailbreak_attempt_rate=1.0 if jb_attempt and jb_attempt.attempt else 0.0,
            jailbreak_compliance_rate=(1.0 if jb_compliance and jb_compliance.compliance else 0.0),
            details=details,
        )

    async def augment(
        self,
        case: EvalCaseRow,
        outcome: EvaluationOutcome,
        *,
        mock: bool,
    ) -> EvaluationOutcome:
        """Compute safety sub-metrics for ``(case, outcome)`` and return a
        new outcome with the breakdown merged + ``safety_violation`` ORed."""
        input_text = _input_text_for(case)
        output_text = outcome.output_text or ""
        result = await self.evaluate(
            input_text=input_text,
            output_text=output_text,
            mock=mock,
        )

        breakdown: dict[str, dict[str, float]] = {}
        if outcome.axis_breakdown:
            for axis, metrics in outcome.axis_breakdown.items():
                breakdown[axis] = dict(metrics)
        breakdown[SAFETY_AXIS] = result.as_axis_metrics()

        merged_judge_raw = dict(outcome.judge_raw or {})
        if result.details:
            merged_judge_raw.setdefault("safety", {}).update(result.details)

        return replace(
            outcome,
            axis_breakdown=breakdown,
            safety_violation=outcome.safety_violation or result.violation,
            judge_raw=merged_judge_raw,
        )


def build_safety_pipeline(spec: PromptSpec, *, mock: bool) -> SafetyPipeline | None:
    """Construct a :class:`SafetyPipeline` from ``spec.safety``.

    Returns ``None`` when the safety block is disabled — the runner then
    skips the augment step entirely (and the gate's safety axis falls back
    to today's boolean-only behaviour).
    """
    safety: SafetySpec = spec.safety
    if not safety.enabled:
        return None
    return SafetyPipeline(
        pii=PresidioPiiDetector(safety.pii),
        jailbreak=JailbreakDetector(safety.jailbreak),
    )


def _input_text_for(case: EvalCaseRow) -> str:
    """Best-effort string view of ``case.input`` for detectors."""
    inp = case.input or {}
    if not isinstance(inp, dict):
        return str(inp)
    for key in ("question", "prompt", "user", "input", "text"):
        val = inp.get(key)
        if isinstance(val, str) and val.strip():
            return val
    # Fallback: concatenate all string values so detectors still see something.
    parts = [v for v in inp.values() if isinstance(v, str)]
    if parts:
        return "\n".join(parts)
    try:
        import json

        return json.dumps(inp, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(inp)
