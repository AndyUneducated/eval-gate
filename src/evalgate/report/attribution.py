"""Tag/intent-wise attribution — surfaces *which slice* of the eval set regressed.

The CI gate's pass-rate delta is a high-level alarm. Attribution turns it into
a root cause: "billing intent dropped 8 pts" rather than "overall pass rate
fell 0.5%".
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import numpy as np

EvalRecord = dict[str, Any]


def _record_tags(record: EvalRecord) -> Iterable[str]:
    raw = record.get("tags") or []
    if isinstance(raw, str):
        return [raw]
    return [str(t) for t in raw]


def tagwise_attribution(
    baseline: Sequence[EvalRecord],
    candidate: Sequence[EvalRecord],
) -> dict[str, dict[str, float]]:
    tags: set[str] = set()
    for record in (*baseline, *candidate):
        tags.update(_record_tags(record))

    out: dict[str, dict[str, float]] = {}
    for tag in sorted(tags):
        b_scores = [float(r.get("score", 0.0)) for r in baseline if tag in _record_tags(r)]
        c_scores = [float(r.get("score", 0.0)) for r in candidate if tag in _record_tags(r)]
        if not b_scores and not c_scores:
            continue
        b_mean = float(np.mean(b_scores)) if b_scores else 0.0
        c_mean = float(np.mean(c_scores)) if c_scores else 0.0
        out[tag] = {
            "baseline": b_mean,
            "candidate": c_mean,
            "delta": c_mean - b_mean,
            "n_baseline": float(len(b_scores)),
            "n_candidate": float(len(c_scores)),
        }
    return out
