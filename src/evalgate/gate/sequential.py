"""Sequential gate orchestration (Phase 15).

Wires the pure sequential engine ([report/sequential.py](../report/sequential.py))
to a live candidate run + a stored baseline run:

1. load the baseline per-case quality scores from a prior ``eval_runs`` id;
2. stream the candidate through ``iter_eval`` (so an early stop genuinely skips
   the remaining, expensive judge calls);
3. pair each candidate record with its baseline score by ``case_id`` and feed
   the diff to the :class:`SequentialGate`;
4. the moment the gate returns a terminal decision, break the stream;
5. assemble a :class:`GateReport` whose **quality** verdict is the sequential
   decision (authoritative), while ``cost`` / ``latency_p95`` / ``safety`` are a
   fixed-N snapshot over the *consumed* cases (reusing ``build_gate_report``).

The candidate stream is injectable (``record_stream``) so tests can drive the
gate deterministically without an LLM.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.core.errors import EvalGateError
from evalgate.core.schemas import (
    AxisMetric,
    EvalRecord,
    GateReport,
    SequentialLook,
    SequentialReport,
)
from evalgate.eval_set import repository as eval_set_repo
from evalgate.evaluator.router import build_router
from evalgate.evaluator.runner import iter_eval
from evalgate.gate.decision import build_gate_report
from evalgate.judge import persistence
from evalgate.judge.prompt_spec import hash_prompt, load_prompt_spec
from evalgate.report import sequential as seq
from evalgate.safety.pipeline import build_safety_pipeline

QUALITY_AXIS = "quality"


class SequentialGateError(EvalGateError, RuntimeError):
    """Raised when the sequential gate cannot run (no baseline / no overlap)."""

    http_status = 422
    exit_code = 2
    slug = "sequential_unrunnable"


@dataclass
class SequentialGateResult:
    run_id: str | None
    report: GateReport


def _record_to_dict(rec: EvalRecord) -> dict[str, Any]:
    return rec.model_dump()


def _baseline_row_to_dict(row: Any) -> dict[str, Any]:
    output = row.output if isinstance(row.output, dict) else {}
    return {
        "case_id": row.eval_case_id,
        "tags": list(row.tags or []),
        "score": float(row.score),
        "cost_usd": float(row.cost_usd),
        "latency_ms": int(row.latency_ms),
        "axis_breakdown": row.axis_breakdown,
        "output": output,
    }


async def _load_baseline(session: AsyncSession, baseline_run_id: str) -> dict[str, dict[str, Any]]:
    """Map ``case_id -> baseline record dict`` for a stored run."""
    rows = await persistence.list_results(session, baseline_run_id)
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.eval_case_id is None:
            continue
        out[row.eval_case_id] = _baseline_row_to_dict(row)
    return out


def _to_look(look: seq.LookRecord) -> SequentialLook:
    return SequentialLook(
        look=look.look,
        n=look.n,
        information_fraction=look.information_fraction,
        z=look.z,
        z_fail=look.z_fail,
        conditional_power=look.conditional_power,
        decision=look.decision,
    )


def assemble_report(
    *,
    baseline_consumed: Sequence[dict[str, Any]],
    candidate_consumed: Sequence[dict[str, Any]],
    gate: seq.SequentialGate,
    decision: seq.Decision,
    cases_consumed: int,
    n_max: int,
) -> GateReport:
    """Build a GateReport: sequential quality verdict + fixed-N snapshot rest.

    ``build_gate_report`` over the consumed cases gives cost / latency_p95 /
    safety + attribution and the quality *numbers*; we then override the quality
    axis' ``passed`` with the sequential decision and recompute the overall
    verdict so the sequential call is authoritative for quality.
    """
    snapshot = build_gate_report(baseline_consumed, candidate_consumed)
    quality_pass = decision == seq.Decision.pass_

    axes: list[AxisMetric] = []
    for axis in snapshot.axes:
        if axis.name == QUALITY_AXIS:
            # Keep the snapshot's numbers; the verdict comes from the
            # sequential test (it also AND's in any quality sub-metric regress).
            sub_regressed = bool(
                axis.sub_metrics and any(not s.passed for s in axis.sub_metrics.values())
            )
            axes.append(axis.model_copy(update={"passed": quality_pass and not sub_regressed}))
        else:
            axes.append(axis)

    passed = all(a.passed for a in axes)
    sequential = SequentialReport(
        decision=decision.value,
        stopped_early=cases_consumed < n_max,
        cases_consumed=cases_consumed,
        n_max=n_max,
        spending=gate.spending,
        mde=gate.mde,
        gamma=gate.gamma,
        looks=[_to_look(look_record) for look_record in gate.looks],
    )
    summary = _sequential_summary(sequential, passed=passed)
    return GateReport(
        passed=passed,
        axes=axes,
        attribution=snapshot.attribution,
        summary=summary,
        sequential=sequential,
    )


def _sequential_summary(sequential: SequentialReport, *, passed: bool) -> str:
    verb = "PASS" if passed else "FAIL"
    stop = (
        f"stopped early at {sequential.cases_consumed}/{sequential.n_max} cases"
        if sequential.stopped_early
        else f"ran all {sequential.n_max} cases"
    )
    return (
        f"Sequential gate {verb} (quality decision={sequential.decision}, {stop}, "
        f"spending={sequential.spending})."
    )


async def run_sequential_gate(
    session: AsyncSession,
    *,
    eval_set: str,
    prompt_path: str,
    baseline_run_id: str,
    look_every: int = seq.DEFAULT_LOOK_EVERY,
    spending: str = seq.DEFAULT_SPENDING,
    mde: float = seq.DEFAULT_MDE,
    gamma: float = seq.DEFAULT_GAMMA,
    alpha: float = seq.DEFAULT_ALPHA,
    judge_model_override: str | None = None,
    mock: bool | None = None,
    record_stream: AsyncIterator[EvalRecord] | None = None,
) -> SequentialGateResult:
    """Run a candidate against ``baseline_run_id`` with early stopping."""
    set_id = await eval_set_repo.resolve_set_id(session, eval_set)
    baseline = await _load_baseline(session, baseline_run_id)
    if not baseline:
        raise SequentialGateError(
            f"baseline run {baseline_run_id!r} has no scored results to pair against"
        )

    # N_max = paired active cases (candidate cases that also exist in baseline).
    active_cases = await eval_set_repo.list_cases(session, set_id)
    paired_ids = {c.id for c in active_cases} & set(baseline)
    n_max = len(paired_ids)
    if n_max < 1:
        raise SequentialGateError(
            "no overlap between baseline run and the candidate eval set's active cases"
        )

    gate = seq.SequentialGate(
        n_max=n_max,
        look_every=look_every,
        spending=spending,
        alpha=alpha,
        mde=mde,
        gamma=gamma,
    )

    run_id: str | None = None
    if record_stream is None:
        stream, run_id = await _build_candidate_stream(
            session,
            set_id=set_id,
            prompt_path=prompt_path,
            judge_model_override=judge_model_override,
            mock=mock,
        )
    else:
        stream = record_stream

    baseline_consumed: list[dict[str, Any]] = []
    candidate_consumed: list[dict[str, Any]] = []
    decision: seq.Decision | None = None

    async for rec in stream:
        base = baseline.get(rec.case_id)
        if base is None:
            # Unpaired candidate case (not in baseline) — still judged + persisted
            # by iter_eval, but excluded from both the sequential test and the
            # fixed-N snapshot so baseline/candidate cover the same population
            # (otherwise cost/latency/safety would compare mismatched case sets).
            continue
        candidate_consumed.append(_record_to_dict(rec))
        baseline_consumed.append(base)
        res = gate.update(rec.score - float(base["score"]))
        if res in (seq.Decision.fail, seq.Decision.pass_):
            decision = res
            break

    if decision is None:
        # Stream exhausted without a terminal look (e.g. fewer paired cases than
        # expected). Fall back to the gate's last verdict, defaulting to pass.
        decision = gate.decision if gate.decision != seq.Decision.continue_ else seq.Decision.pass_

    report = assemble_report(
        baseline_consumed=baseline_consumed,
        candidate_consumed=candidate_consumed,
        gate=gate,
        decision=decision,
        cases_consumed=len(baseline_consumed),
        n_max=n_max,
    )
    return SequentialGateResult(run_id=run_id, report=report)


async def _build_candidate_stream(
    session: AsyncSession,
    *,
    set_id: str,
    prompt_path: str,
    judge_model_override: str | None,
    mock: bool | None,
) -> tuple[AsyncIterator[EvalRecord], str]:
    """Create the eval_run + return ``iter_eval`` over the candidate prompt."""
    from evalgate.core.config import is_mock_llm

    spec = load_prompt_spec(prompt_path)
    if judge_model_override:
        new_judges = [j.model_copy(update={"model": judge_model_override}) for j in spec.judges]
        spec = spec.model_copy(update={"judges": new_judges})

    use_mock = is_mock_llm() if mock is None else mock
    router = build_router(spec, mock=use_mock)
    safety_pipeline = build_safety_pipeline(spec, mock=use_mock)

    run = await persistence.create_run(
        session,
        eval_set_id=set_id,
        prompt_path=str(prompt_path),
        prompt_hash=hash_prompt(prompt_path),
        candidate_model=spec.candidate.model,
        judge_model=router.label(),
    )
    stream = iter_eval(
        session,
        eval_set_id=set_id,
        spec=spec,
        run_id=run.id,
        router=router,
        mock=use_mock,
        safety_pipeline=safety_pipeline,
    )
    return stream, run.id
