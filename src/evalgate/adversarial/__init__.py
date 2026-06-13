"""Phase 14 · Adversarial Case Synth (红队自动出题).

A closed-loop red-teaming flywheel: a generator-LLM auto-creates tricky cases
for the weakest tag, which enter a human-review (``pending``) lifecycle before
joining the eval set. The two public surfaces:

- :mod:`evalgate.adversarial.synth` — pure generation (LLM → candidate cases).
- :mod:`evalgate.adversarial.repository` — persistence + the
  pending→active/archived review lifecycle + a hit-rate stats report.
"""

from __future__ import annotations

from evalgate.adversarial.synth import (
    ADVERSARIAL_TEMPLATES,
    GeneratedCase,
    synthesize,
)

__all__ = [
    "ADVERSARIAL_TEMPLATES",
    "GeneratedCase",
    "synthesize",
]
