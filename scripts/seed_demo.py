"""Generate deterministic baseline/candidate fixtures for the demo CI gate run.

The candidate has a hand-rolled regression on the `billing` tag so the
attribution layer has something to surface in the PR comment.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OUT_DIR = Path("examples/fixtures")
TAGS = ("billing", "qa", "general")
N_CASES = 60


def _make_records(seed: int, regress_tag: str | None = None) -> list[dict]:
    rng = random.Random(seed)
    records: list[dict] = []
    for i in range(N_CASES):
        tag = rng.choice(TAGS)
        score = rng.uniform(0.78, 0.95)
        if regress_tag and tag == regress_tag:
            score -= 0.22
        records.append(
            {
                "case_id": f"case-{i:03d}",
                "tags": [tag],
                "score": round(max(0.0, min(1.0, score)), 3),
                "cost_usd": round(rng.uniform(0.005, 0.02), 4),
                "latency_ms": rng.randint(800, 1500),
            }
        )
    return records


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    baseline = _make_records(seed=1)
    candidate = _make_records(seed=1, regress_tag="billing")
    (OUT_DIR / "baseline.json").write_text(json.dumps({"records": baseline}, indent=2))
    (OUT_DIR / "candidate.json").write_text(json.dumps({"records": candidate}, indent=2))
    print(f"wrote {OUT_DIR}/baseline.json and {OUT_DIR}/candidate.json")


if __name__ == "__main__":
    main()
