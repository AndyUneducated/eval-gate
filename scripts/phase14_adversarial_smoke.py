"""Phase 14 end-to-end smoke: red-team flywheel over a billing eval set.

Flow:

1. seed a ``billing`` set of happy-path cases + run a clean **baseline**.
2. ``adversarial.generate`` 10 tricky ``billing`` cases (status=pending).
3. run the set again -> the 10 pending cases are EXCLUDED (the runner only
   sees ``active``). This is the safety invariant: unreviewed cases can never
   leak into a gate run.
4. ``review`` -> approve 6, reject 4.
5. run the set as the **candidate** -> now the 6 approved cases ARE scored.
6. ``adversarial.stats`` reports the hit-rate (hit = score < 0.5), and the
   gate's safety axis regresses (the approved injection case exposes attack
   surface the baseline never had).

Deterministic + offline under ``EVALGATE_MOCK_LLM=1`` (the generator + judge
both mock). Real mode (``EVALGATE_MOCK_LLM=0``) hits Ollama and additionally
expects real quality hits.

Usage::

    EVALGATE_MOCK_LLM=1 PYTHONPATH='src:.' python scripts/phase14_adversarial_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from _smoke import EXIT_ERROR, EXIT_FAILED, EXIT_OK, mock_from_env
from examples.adversarial_demo.seed import SET_NAME, TAG, seed
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from evalgate.adversarial import repository as adv_repo
from evalgate.core.schemas import CaseSource, CaseStatus
from evalgate.db.models import Base
from evalgate.evaluator import runner as eval_runner
from evalgate.gate.decision import build_gate_report

REPO = Path(__file__).resolve().parent.parent
PROMPT_YAML = REPO / "examples" / "adversarial_demo" / "prompts" / "adversarial_candidate.yaml"

K = 10
N_APPROVE = 6


async def _ensure_schema(database_url: str) -> None:
    if not database_url.startswith("sqlite"):
        return
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


async def _run(database_url: str, set_id: str, *, mock: bool) -> dict:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            result = await eval_runner.run_eval(
                session,
                eval_set_id_or_name=set_id,
                prompt_path=str(PROMPT_YAML),
                mock=mock,
            )
        return {
            "run_id": result.run_id,
            "mean_score": result.mean_score,
            "records": [r.model_dump(mode="json") for r in result.records],
        }
    finally:
        await engine.dispose()


def _case_ids(payload: dict) -> set[str]:
    return {r["case_id"] for r in payload["records"]}


async def _amain() -> int:
    mock = mock_from_env()
    print(f"mock={mock}")

    if not os.environ.get("DATABASE_URL"):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{db_path}"
        print(f"using ephemeral DB: {os.environ['DATABASE_URL']}")
    database_url = os.environ["DATABASE_URL"]

    await _ensure_schema(database_url)
    set_id = await seed(database_url)
    print(f"seeded set={SET_NAME!r} ({set_id})")

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # 1) baseline run (happy-path cases only).
    baseline = await _run(database_url, set_id, mock=mock)
    base_ids = _case_ids(baseline)
    print(f"baseline run: {len(base_ids)} cases, mean={baseline['mean_score']:.3f}")

    # 2) generate K pending adversarial cases for the weak tag.
    async with factory() as session:
        created = await adv_repo.generate_into_set(
            session, set_id_or_name=set_id, tag=TAG, k=K, mock=mock
        )
    print(f"generated {len(created)} adversarial cases")
    if not created:
        print("ERROR: generator produced no cases", file=sys.stderr)
        await engine.dispose()
        return EXIT_ERROR
    adversarial_ids = {c.id for c in created}
    for c in created:
        if c.status != CaseStatus.pending.value or c.source != CaseSource.adversarial.value:
            print(
                f"FAILED: generated case {c.id} has status={c.status} source={c.source} "
                "(want pending/adversarial)",
                file=sys.stderr,
            )
            await engine.dispose()
            return EXIT_FAILED

    # 3) a run now must STILL exclude the pending cases.
    pending_run = await _run(database_url, set_id, mock=mock)
    leaked = adversarial_ids & _case_ids(pending_run)
    if leaked:
        print(
            f"FAILED: {len(leaked)} pending adversarial cases leaked into a run: {leaked}",
            file=sys.stderr,
        )
        await engine.dispose()
        return EXIT_FAILED
    print(f"exclusion OK: pending run still has {len(_case_ids(pending_run))} cases (no leak)")

    # 4) review: approve N, reject the rest.
    ordered = list(created)
    approved = ordered[:N_APPROVE]
    rejected = ordered[N_APPROVE:]
    async with factory() as session:
        for c in approved:
            await adv_repo.review_case(session, case_id=c.id, decision="approve")
        for c in rejected:
            await adv_repo.review_case(session, case_id=c.id, decision="reject")
        still_pending = await adv_repo.list_pending(session, set_id_or_name=set_id)
    if still_pending:
        print(f"FAILED: {len(still_pending)} cases still pending after review", file=sys.stderr)
        await engine.dispose()
        return EXIT_FAILED
    approved_ids = {c.id for c in approved}
    print(f"reviewed: approved {len(approved)}, rejected {len(rejected)}")

    # 5) candidate run now includes the approved adversarial cases.
    candidate = await _run(database_url, set_id, mock=mock)
    cand_ids = _case_ids(candidate)
    missing = approved_ids - cand_ids
    if missing:
        print(
            f"FAILED: {len(missing)} approved cases missing from candidate run: {missing}",
            file=sys.stderr,
        )
        await engine.dispose()
        return EXIT_FAILED
    if rejected and (cand_ids & {c.id for c in rejected}):
        print("FAILED: a rejected (archived) case appeared in the candidate run", file=sys.stderr)
        await engine.dispose()
        return EXIT_FAILED
    print(f"inclusion OK: candidate run has {len(cand_ids)} cases incl. all approved")

    # 6) gate report: the approved injection case adds attack surface the
    #    baseline never had, so the safety jailbreak sub-axis regresses.
    report = build_gate_report(baseline["records"], candidate["records"])
    safety = next((a for a in report.axes if a.name == "safety"), None)
    if safety is None or not safety.sub_metrics:
        print("ERROR: gate report missing safety.sub_metrics", file=sys.stderr)
        await engine.dispose()
        return EXIT_ERROR
    attempt = safety.sub_metrics.get("jailbreak_attempt_rate")
    if attempt is None or attempt.delta <= 0:
        print(
            "FAILED: jailbreak_attempt_rate did not increase on candidate "
            f"(delta={None if attempt is None else attempt.delta})",
            file=sys.stderr,
        )
        await engine.dispose()
        return EXIT_FAILED
    print(f"safety regression OK: jailbreak_attempt_rate delta={attempt.delta:+.3f}")

    # 7) stats: hit-rate over the approved adversarial cases.
    async with factory() as session:
        st = await adv_repo.stats(session, set_id_or_name=set_id)
    print("adversarial stats:", json.dumps(st.to_dict(), indent=2))
    await engine.dispose()

    if st.total != N_APPROVE or st.evaluated != N_APPROVE:
        print(
            f"FAILED: stats total={st.total} evaluated={st.evaluated} (want {N_APPROVE})",
            file=sys.stderr,
        )
        return EXIT_FAILED

    if mock:
        # Mock judge returns a flat 0.5, so quality hits (<0.5) don't fire; the
        # deterministic weakness signal is the safety regression above.
        print("OK: mock flywheel — generate -> exclude -> review -> include -> safety regression")
    else:
        # Real candidate genuinely fails the tricky cases.
        if st.hits < 1:
            print(
                f"FAILED: real candidate produced no hits (hits={st.hits}); "
                "expected the weak prompt to fail adversarial cases",
                file=sys.stderr,
            )
            return EXIT_FAILED
        print(f"OK: real flywheel — {st.hits}/{st.evaluated} adversarial hits (score<0.5)")
    return EXIT_OK


def main() -> None:
    sys.exit(asyncio.run(_amain()))


if __name__ == "__main__":
    main()
