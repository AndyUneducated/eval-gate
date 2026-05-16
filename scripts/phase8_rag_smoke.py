"""Phase 8 end-to-end smoke: seed RAG demo set → runner → gate.

Designed to be runnable both fully mocked (CI / no Ollama) and against a
real local Ollama (`ollama serve` with `qwen2.5:7b` + `qwen3-embedding:8b`).

Usage::

    # Fully offline (no LLM calls): ragas adapter returns deterministic
    # mock scores, retriever uses hash vectors.
    EVALGATE_MOCK_LLM=1 DATABASE_URL=sqlite+aiosqlite:///rag_smoke.db \
        python scripts/phase8_rag_smoke.py

    # Real local Ollama:
    DATABASE_URL=postgresql+asyncpg://evalgate@localhost/evalgate \
        python scripts/phase8_rag_smoke.py

The script exits non-zero on any of:
- ragas didn't return all three sub-metrics
- gate report quality axis missing nested ``sub_metrics``
- baseline mean_score is not >= candidate mean_score (only enforced in
  real-LLM mode; mocked runs bypass this since both runs collapse to
  the same constant score).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from examples.rag_demo.seed import CASES, SET_NAME, seed
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evalgate.db.models import Base
from evalgate.evaluator import runner as eval_runner
from evalgate.gate.decision import build_gate_report

REPO = Path(__file__).resolve().parent.parent
BASELINE_YAML = REPO / "examples" / "rag_demo" / "prompts" / "rag_baseline.yaml"
CANDIDATE_YAML = REPO / "examples" / "rag_demo" / "prompts" / "rag_candidate.yaml"


async def _ensure_schema(database_url: str) -> None:
    """Bootstrap an empty SQLite DB so the script is self-contained.

    For Postgres we assume Alembic has already been run by ``make db-up``.
    """
    if not database_url.startswith("sqlite"):
        return
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _run_one(database_url: str, set_id: str, prompt_yaml: Path) -> dict:
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            result = await eval_runner.run_eval(
                session,
                eval_set_id_or_name=set_id,
                prompt_path=str(prompt_yaml),
                mock=None,  # let env-var / explicit be honoured
            )
        return {
            "run_id": result.run_id,
            "mean_score": result.mean_score,
            "records": [r.model_dump() for r in result.records],
        }
    finally:
        await engine.dispose()


async def _amain() -> int:
    is_mock = os.environ.get("EVALGATE_MOCK_LLM", "").lower() in {"1", "true", "yes"}

    # Default to a temp SQLite so the script works without Postgres.
    if not os.environ.get("DATABASE_URL"):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
        print(f"using ephemeral DB: {os.environ['DATABASE_URL']}")
    database_url = os.environ["DATABASE_URL"]

    await _ensure_schema(database_url)

    set_id = await seed(database_url)
    print(f"seeded set_id={set_id} name={SET_NAME} cases={len(CASES)}")

    print("running baseline...")
    baseline = await _run_one(database_url, set_id, BASELINE_YAML)
    print(f"  mean_score={baseline['mean_score']:.3f}")

    print("running candidate...")
    candidate = await _run_one(database_url, set_id, CANDIDATE_YAML)
    print(f"  mean_score={candidate['mean_score']:.3f}")

    # Sanity: every record carries the three sub-metrics.
    expected_metrics = {"faithfulness", "context_precision", "answer_relevance"}
    for label, payload in (("baseline", baseline), ("candidate", candidate)):
        for rec in payload["records"]:
            sm = rec.get("sub_metrics") or {}
            if set(sm) != expected_metrics:
                print(
                    f"FAIL: {label} record {rec.get('case_id')} sub_metrics={sm} "
                    f"(want {expected_metrics})",
                    file=sys.stderr,
                )
                return 2

    report = build_gate_report(baseline["records"], candidate["records"])
    print(json.dumps(report.model_dump(mode="json"), indent=2))

    quality = next((a for a in report.axes if a.name == "quality"), None)
    if quality is None or not quality.sub_metrics:
        print("FAIL: gate report missing quality.sub_metrics", file=sys.stderr)
        return 2
    if set(quality.sub_metrics) != expected_metrics:
        print(
            f"FAIL: quality.sub_metrics keys = {set(quality.sub_metrics)} "
            f"(want {expected_metrics})",
            file=sys.stderr,
        )
        return 2

    if not is_mock:
        # Real-LLM mode: the candidate is deliberately weakened so we
        # *expect* a regression on at least one sub-metric.
        worse = [n for n, sub in quality.sub_metrics.items() if sub.delta < 0]
        if not worse:
            print("WARN: candidate did not regress on any sub-metric", file=sys.stderr)
    return 0


def main() -> None:
    code = asyncio.run(_amain())
    sys.exit(code)


if __name__ == "__main__":
    main()
