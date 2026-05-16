"""Phase 10: PII + jailbreak detection that fills ``axis_breakdown["safety"]``.

The package layout mirrors :mod:`evalgate.evaluator`:

- :mod:`evalgate.safety.detector`    — protocols + dataclasses
- :mod:`evalgate.safety.pii`         — Presidio-backed PII detector
- :mod:`evalgate.safety.jailbreak`   — keyword + optional LLM classifier
- :mod:`evalgate.safety.pipeline`    — orchestrator wired into the runner

The runner pulls :func:`build_safety_pipeline` and calls
``await pipeline.augment(case, outcome)`` after each evaluator returns. The
pipeline never raises — bad detectors degrade to ``rate=0.0`` so a single
broken detector can't fail the run.
"""

from evalgate.safety.detector import SafetyResult
from evalgate.safety.pipeline import SafetyPipeline, build_safety_pipeline

__all__ = ["SafetyPipeline", "SafetyResult", "build_safety_pipeline"]
