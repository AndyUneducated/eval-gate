"""Phase 7 smoke: 10 cases -> run -> BadCaseFinder -> promote 3 to a harder set.

Closes the Phase 7 exit criterion: "from 10 mock cases, auto-pick 3 badcases
and one-click promote them into a new eval_set". Runs entirely on a temp
sqlite + `EVALGATE_MOCK_LLM=1` by default (no Ollama needed). Set
`EVALGATE_MOCK_LLM=0` plus a real `ollama list`-listed model to do the same
flow against real LLMs.

Usage:
    EVALGATE_MOCK_LLM=1 uv run python scripts/phase7_badcase_smoke.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

from _smoke import EXIT_ERROR, EXIT_FAILED, EXIT_OK, mock_from_env

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(tempfile.gettempdir()) / "evalgate-phase7-smoke.db"

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB_PATH}"

_PROMPT_YAML = """
name: smoke
candidate:
  model: ollama/qwen3.5:9b
  user_template: "{prompt}"
  params: {}
judges:
  - model: ollama/qwen3.5:9b
    rubric: "rate"
    params: {}
judge_policy:
  mode: pointwise
  k: 1
  position_swap: false
  concurrency: 2
"""


async def _bootstrap_schema():
    from sqlalchemy.ext.asyncio import create_async_engine

    from evalgate.db.models import Base

    engine = create_async_engine(os.environ["DATABASE_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _seed_eval_sets(prompt_path: Path, *, mock: bool):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from evalgate.eval_set import repository
    from evalgate.evaluator import runner

    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        src = await repository.create_eval_set(session, name="phase7-src")
        await repository.create_eval_set(session, name="phase7-hard")
        for i in range(10):
            await repository.add_case(
                session,
                set_id=src.id,
                input={"prompt": f"q{i}: billing question {i}"},
                expected={"output": f"reference answer {i}"},
                tags=["billing"],
            )

        result = await runner.run_eval(
            session,
            eval_set_id_or_name="phase7-src",
            prompt_path=str(prompt_path),
            mock=mock,
        )
    await engine.dispose()
    return result


async def _list_and_promote(*, mock: bool) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from evalgate.badcase import finder
    from evalgate.badcase import repository as badcase_repo
    from evalgate.eval_set import repository as set_repo

    engine = create_async_engine(os.environ["DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        badcases = await finder.find(session, strategy="uncertainty", limit=3, mock=mock)

    print(f"\nFound {len(badcases)} uncertainty badcases:")
    for bc in badcases:
        print(
            f"  {bc.eval_result_id[:8]}…  conf={bc.judge_confidence}  "
            f"score={bc.score}  reason={bc.reason}"
        )

    async with factory() as session:
        for bc in badcases:
            membership = await badcase_repo.promote_result_to_set(
                session,
                eval_result_id=bc.eval_result_id,
                target_set_id_or_name="phase7-hard",
                strategy="uncertainty",
                extra_tags=["smoke"],
            )
            print(
                f"  promoted -> membership={membership.id[:8]}…  "
                f"case={membership.eval_case_id[:8]}…  "
                f"set={membership.eval_set_id[:8]}…"
            )

    async with factory() as session:
        dst_id = await set_repo.resolve_set_id(session, "phase7-hard")
        dst_cases = await set_repo.list_cases(session, dst_id)
    print(f"\nTarget eval_set phase7-hard now has {len(dst_cases)} cases.")
    await engine.dispose()
    return len(dst_cases)


def main() -> int:
    mock = mock_from_env(default=True)
    print(f"mode={'mock' if mock else 'real (Ollama)'}")
    prompt_path = Path(tempfile.gettempdir()) / "evalgate-phase7-prompt.yaml"
    prompt_path.write_text(_PROMPT_YAML)
    try:
        asyncio.run(_bootstrap_schema())
        result = asyncio.run(_seed_eval_sets(prompt_path, mock=mock))
        print(f"Ran {result.total_cases} cases; mean_score={result.mean_score}")
        if result.total_cases != 10:
            print(f"ERROR: expected 10 cases, ran {result.total_cases}", file=sys.stderr)
            return EXIT_ERROR
        n_promoted = asyncio.run(_list_and_promote(mock=mock))
        if n_promoted <= 0:
            print("FAILED: BadCaseFinder promoted no cases to phase7-hard", file=sys.stderr)
            return EXIT_FAILED
        return EXIT_OK
    finally:
        if DB_PATH.exists():
            DB_PATH.unlink()
        if prompt_path.exists():
            prompt_path.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
