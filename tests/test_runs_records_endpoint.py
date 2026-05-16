"""GET /v1/runs/{run_id}/records — round-trip per-case results back as EvalRecord-shaped JSON."""

from __future__ import annotations

from httpx import AsyncClient

from evalgate.eval_set import repository as set_repo
from evalgate.judge import persistence


async def test_records_404_on_missing_run(client: AsyncClient) -> None:
    resp = await client.get("/v1/runs/missing/records")
    assert resp.status_code == 404


async def test_records_round_trip_with_axis_breakdown(
    client: AsyncClient, db_session_factory
) -> None:
    async with db_session_factory() as session:
        s = await set_repo.create_eval_set(session, name="rt")
        case_a = await set_repo.add_case(
            session,
            set_id=s.id,
            input={"prompt": "q1"},
            tags=["billing"],
        )
        case_b = await set_repo.add_case(
            session,
            set_id=s.id,
            input={"prompt": "q2"},
            tags=["billing", "rag"],
        )
        run = await persistence.create_run(
            session,
            eval_set_id=s.id,
            prompt_path="p.yaml",
            prompt_hash="h",
            candidate_model="m",
            judge_model="m",
        )
        await persistence.add_result(
            session,
            run_id=run.id,
            case_id=case_a.id,
            tags=case_a.tags,
            output_text="generic answer",
            score=0.9,
            reason=None,
            cost_usd=0.001,
            latency_ms=120,
        )
        await persistence.add_result(
            session,
            run_id=run.id,
            case_id=case_b.id,
            tags=case_b.tags,
            output_text="rag answer",
            score=0.7,
            reason=None,
            cost_usd=0.002,
            latency_ms=240,
            safety_violation=False,
            axis_breakdown={
                "quality": {"faithfulness": 0.8, "context_precision": 0.6},
                "safety": {"pii_input_rate": 0.0},
            },
            retrieved_contexts=["ctx-1", "ctx-2"],
        )

    body = (await client.get(f"/v1/runs/{run.id}/records")).json()
    assert body["run_id"] == run.id
    records = body["records"]
    assert len(records) == 2

    by_case = {r["case_id"]: r for r in records}
    rec_a = by_case[case_a.id]
    assert rec_a["score"] == 0.9
    assert rec_a["cost_usd"] == 0.001
    assert rec_a["latency_ms"] == 120
    assert rec_a["safety_violation"] is False
    assert rec_a["axis_breakdown"] is None
    assert rec_a["output_text"] == "generic answer"
    assert rec_a["eval_run_id"] == run.id

    rec_b = by_case[case_b.id]
    assert rec_b["axis_breakdown"] == {
        "quality": {"faithfulness": 0.8, "context_precision": 0.6},
        "safety": {"pii_input_rate": 0.0},
    }
    assert rec_b["retrieved_contexts"] == ["ctx-1", "ctx-2"]
    assert set(rec_b["tags"]) == {"billing", "rag"}
    assert rec_b["eval_result_id"]


async def test_records_can_feed_evals_run(client: AsyncClient, db_session_factory) -> None:
    """Two seeded runs → fetch records via HTTP → POST /v1/evals/run → GateReport."""
    async with db_session_factory() as session:
        s = await set_repo.create_eval_set(session, name="gate-rt")
        cases = []
        for i in range(4):
            cases.append(
                await set_repo.add_case(
                    session,
                    set_id=s.id,
                    input={"prompt": f"q{i}"},
                    tags=["billing"],
                )
            )
        baseline = await persistence.create_run(
            session,
            eval_set_id=s.id,
            prompt_path="p.yaml",
            prompt_hash="h1",
            candidate_model="m",
            judge_model="m",
        )
        candidate = await persistence.create_run(
            session,
            eval_set_id=s.id,
            prompt_path="p.yaml",
            prompt_hash="h2",
            candidate_model="m",
            judge_model="m",
        )
        for c in cases:
            await persistence.add_result(
                session,
                run_id=baseline.id,
                case_id=c.id,
                tags=c.tags,
                output_text="ok",
                score=0.9,
                reason=None,
                cost_usd=0.001,
                latency_ms=100,
            )
            await persistence.add_result(
                session,
                run_id=candidate.id,
                case_id=c.id,
                tags=c.tags,
                output_text="bad",
                score=0.4,
                reason=None,
                cost_usd=0.002,
                latency_ms=200,
            )

    baseline_records = (await client.get(f"/v1/runs/{baseline.id}/records")).json()["records"]
    candidate_records = (await client.get(f"/v1/runs/{candidate.id}/records")).json()["records"]

    report = (
        await client.post(
            "/v1/evals/run",
            json={"baseline": baseline_records, "candidate": candidate_records},
        )
    ).json()

    assert {a["name"] for a in report["axes"]} == {
        "quality",
        "cost",
        "latency_p95",
        "safety",
    }
    quality = next(a for a in report["axes"] if a["name"] == "quality")
    assert quality["baseline"] > quality["candidate"]
    assert quality["delta"] < 0
