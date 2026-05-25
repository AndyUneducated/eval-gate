"""Regression guard: legacy Ollama tags must not creep back into active code.

When we bumped the default Ollama models (``qwen2.5:7b`` -> ``qwen3.5:9b``
and ``qwen2.5:32b`` -> ``qwen3.6:27b``), any straggler reference would
silently route candidate / judge / safety / badcase calls back to a model
the team no longer maintains. This test fails loudly on any such
straggler in the directories we actively own.

Scope is deliberately narrow:

- We scan ``src/``, ``examples/``, ``scripts/`` and ``tests/`` — the
  source-of-truth surfaces.
- We DO NOT scan ``docs/`` or ``JOURNAL.md``: those are historical
  records of which model was used at a given milestone; rewriting them
  is revisionism.
- The forbidden patterns are assembled at runtime so this file itself
  doesn't trip the guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("src", "examples", "scripts", "tests")
SCAN_SUFFIXES = (".py", ".yaml", ".yml")

# Build the patterns at runtime so this test file itself doesn't match.
_LEGACY_PREFIX = "ollama/" + "qwen2.5"
LEGACY_PATTERNS = (f"{_LEGACY_PREFIX}:7b", f"{_LEGACY_PREFIX}:32b")


def _iter_scanned_files() -> list[Path]:
    files: list[Path] = []
    for sub in SCAN_DIRS:
        root = REPO_ROOT / sub
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix not in SCAN_SUFFIXES:
                continue
            if path.resolve() == Path(__file__).resolve():
                continue
            files.append(path)
    return files


@pytest.mark.parametrize("pattern", LEGACY_PATTERNS)
def test_no_legacy_model_tags_in_active_code(pattern: str) -> None:
    offenders: list[str] = []
    for path in _iter_scanned_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if pattern in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        f"Found legacy model tag {pattern!r} in active code. "
        f"Update these files to the new tag: {offenders}"
    )
