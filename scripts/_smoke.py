"""Shared helpers + conventions for the phase smoke scripts.

Two things every smoke should agree on:

1. **Mock vs real is decided in exactly one place** — :func:`mock_from_env`,
   driven by ``EVALGATE_MOCK_LLM``. No smoke hard-codes ``mock=True`` anymore,
   so ``EVALGATE_MOCK_LLM=0`` always means "hit the real LM".

2. **Exit codes mean the same thing everywhere** (validation smokes):

   ===========  =====  ===================================================
   constant     code   meaning
   ===========  =====  ===================================================
   EXIT_OK        0    smoke ran and every expectation held
   EXIT_FAILED    1    an expectation did NOT hold (the thing it guards broke)
   EXIT_ERROR     2    plumbing/connectivity error (couldn't even run)
   ===========  =====  ===================================================

   ``scripts/phase12_ci_gate.py`` is the exception by design: it *is* the CI
   gate, so its exit code is the gate verdict (0 pass / 1 regression / 2 error)
   — same numbers, but ``1`` there is a healthy "gate did its job" outcome.

Importing this module also switches stdout/stderr to line buffering so that
interleaved progress + error lines keep their real order when redirected to a
log file (otherwise block-buffered stdout flushes last and stderr "FAIL" lines
appear to come first).
"""

from __future__ import annotations

import os
import sys

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_ERROR = 2

_TRUTHY = {"1", "true", "yes", "on"}


def mock_from_env(*, default: bool = False) -> bool:
    """Whether to run against the deterministic mock instead of a real LM.

    ``EVALGATE_MOCK_LLM`` unset -> ``default``; otherwise parsed as a boolean so
    that ``EVALGATE_MOCK_LLM=0`` correctly means "real" (the old truthy-string
    check treated the literal ``"0"`` as mock).
    """
    raw = os.environ.get("EVALGATE_MOCK_LLM")
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def enable_line_buffering() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(line_buffering=True)


enable_line_buffering()
