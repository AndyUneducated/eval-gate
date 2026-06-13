"""`evalgate` command-line entrypoint.

Subcommands:

    evalgate gate --baseline baseline.json --candidate candidate.json
        Run the multi-axis CI gate. Process exits 0 if the gate passes
        and 1 if any axis regresses with a statistically significant delta.

    evalgate eval-set create --name X [--description ...]
        Create a new eval set; prints the resulting JSON row.

    evalgate eval-set add --set <id-or-name> --from-trace <trace_id>
                          [--tag tag1 --tag tag2] [--task-type rag|agent|generic]
        Promote a stored trace into an eval case. The first LLM-looking span
        becomes the case input/expected (see `ingest.case_extract`).

    evalgate eval-set show --set <id-or-name>
        Dump set metadata + all its cases as JSON.

    evalgate eval-set add-agent-case --set <id-or-name> --question "..." --step '{"tool":"...","args":{...}}'
        Add a hand-authored agent-eval case with an expected trajectory.

    evalgate run --eval-set <id-or-name> --prompt path/to/prompt.yaml --out r.json
                 [--judge-model M] [--mock] [--limit N]
                 [--k K] [--concurrency N] [--policy-mode pointwise|pairwise]
        Run every case in the set through (candidate LLM -> MultiJudge stack),
        persist to eval_runs/eval_results/eval_judge_calls, and write a
        gate-ready records JSON. `--k / --concurrency / --policy-mode`
        override the matching keys under `judge_policy:` in the YAML and
        are mainly used by `scripts/phase6_variance.py`.

    evalgate run --eval-set <id-or-name> --prompt prompt.yaml --out report.json
                 --gate-mode sequential --baseline-run <run_id>
                 [--look-every 5] [--spending obf|pocock] [--mde 0.03] [--gamma 0.2]
        Phase 15 sequential gate: stream the candidate against a stored baseline
        run (paired by case_id) and stop early — FAIL via Lan-DeMets
        alpha-spending, PASS via stochastic curtailment — to skip the remaining
        judge calls. `--out` receives the GateReport JSON; exit code is the
        gate verdict (0 pass / 1 fail / 2 error).

    evalgate badcase list --strategy {uncertainty|outlier|llm}
                          [--run <run_id>] [--limit 20] [--mock]
        Print the top-N badcases from eval_results. Output is a JSON array
        of BadCase rows; copy an `eval_result_id` into `promote` below.

    evalgate badcase promote --result <eval_result_id>
                             --eval-set <target_id_or_name>
                             [--strategy uncertainty] [--tag X --tag Y]
        Add the underlying eval_case to the target set via a membership row
        in `eval_case_set_memberships` (Phase 7.5; no payload duplication).
        Refuses (rc=1) if the case is already a member.

    evalgate shadow report --candidate-hash <h> [--window-hours 24]
        Read-only: aggregate the trailing window of shadow_observations for
        a candidate prompt hash into a 4-axis gate report (Phase 13).

    evalgate shadow rollup --candidate-hash <h> [--window-hours 24]
        Same aggregation, but persists a shadow_reports snapshot and fires the
        regression webhook (Slack-compatible) when any axis regresses.
        Exits 1 when the rolling report FAILs (a candidate regression).

DB-touching subcommands connect to `DATABASE_URL` directly (no HTTP), so this
works in CI without spinning up the API server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from evalgate.adversarial import repository as adversarial_repo
from evalgate.badcase import finder as badcase_finder
from evalgate.badcase import repository as badcase_repo
from evalgate.calibration import repository as calibration_repo
from evalgate.core.config import get_settings, is_mock_llm
from evalgate.core.errors import EvalGateError
from evalgate.core.schemas import EvalCaseOut, EvalSetOut, HumanLabel, TaskKind
from evalgate.db.session import SessionLocal
from evalgate.eval_set import repository
from evalgate.evaluator import runner as eval_runner
from evalgate.gate.decision import build_gate_report
from evalgate.report import sequential as seq_engine
from evalgate.shadow import rollup as shadow_rollup


def _load_records(path: Path) -> list[dict[str, Any]]:
    with path.open() as f:
        data = json.load(f)
    if isinstance(data, dict) and "records" in data:
        records = data["records"]
    elif isinstance(data, list):
        records = data
    else:
        raise ValueError(f"unexpected schema in {path}: expected list or {{'records': [...]}}")
    if not all(isinstance(r, dict) for r in records):
        raise ValueError(f"all records in {path} must be objects")
    return records


def _cmd_gate(args: argparse.Namespace) -> int:
    baseline = _load_records(args.baseline)
    candidate = _load_records(args.candidate)
    report = build_gate_report(baseline, candidate)
    payload = report.model_dump_json(indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload)
    print(payload)
    return 0 if report.passed else 1


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


def _print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=_json_default))


def _set_to_dict(row) -> dict[str, Any]:
    # Reuse the API response model so the CLI and REST share one row->payload
    # mapping (``mode="json"`` renders datetimes / enums as the CLI expects).
    return EvalSetOut.model_validate(row).model_dump(mode="json")


def _case_to_dict(row) -> dict[str, Any]:
    return EvalCaseOut.model_validate(row).model_dump(mode="json")


def _parse_step_json(values: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, raw in enumerate(values):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--step[{idx}] is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"--step[{idx}] must be a JSON object")
        tool = payload.get("tool")
        args = payload.get("args", {})
        if not isinstance(tool, str) or not tool.strip():
            raise ValueError(f"--step[{idx}] missing non-empty string field `tool`")
        if not isinstance(args, dict):
            raise ValueError(f"--step[{idx}] field `args` must be an object")
        out.append({"tool": tool, "args": args})
    return out


async def _with_session(fn: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
    async with SessionLocal() as session:
        return await fn(session)


def run_db_command(body: Callable[[AsyncSession], Awaitable[dict[str, Any]]]) -> int:
    """Run an async DB-touching command body and print its result.

    ``body`` returns the success payload dict (no status threading). Any
    :class:`EvalGateError` it raises is mapped — in one place — to its declared
    ``exit_code`` + ``{"error", "detail"}`` JSON, mirroring the API's single
    exception handler. Returns 0 on success.
    """
    try:
        payload = asyncio.run(_with_session(body))
    except EvalGateError as exc:
        _print(exc.payload())
        return exc.exit_code
    _print(payload)
    return 0


def _cmd_eval_set_create(args: argparse.Namespace) -> int:
    async def body(session: AsyncSession):
        row = await repository.create_eval_set(
            session, name=args.name, description=args.description
        )
        return _set_to_dict(row)

    return run_db_command(body)


def _cmd_eval_set_add(args: argparse.Namespace) -> int:
    task_type = TaskKind(args.task_type) if args.task_type else None

    async def body(session: AsyncSession):
        set_id = await repository.resolve_set_id(session, args.set)
        row = await repository.add_case_from_trace(
            session,
            set_id=set_id,
            trace_id=args.from_trace,
            extra_tags=list(args.tag or []),
            task_type_override=task_type,
        )
        return _case_to_dict(row)

    return run_db_command(body)


def _cmd_eval_set_add_rag(args: argparse.Namespace) -> int:
    """Add a hand-authored RAG case (question + gold answer + reference contexts).

    Used by the Phase 8 demo seeder; for traces, ``eval-set add --from-trace``
    still applies and now also carries any contexts ``case_extract`` recovers.
    """

    async def body(session: AsyncSession):
        set_id = await repository.resolve_set_id(session, args.set)
        row = await repository.add_case(
            session,
            set_id=set_id,
            task_type=TaskKind.rag,
            input={"question": args.question},
            expected={"answer": args.answer},
            tags=list(args.tag or []),
            retrieved_contexts=list(args.context or []),
        )
        return _case_to_dict(row)

    return run_db_command(body)


def _cmd_eval_set_add_agent(args: argparse.Namespace) -> int:
    """Add a hand-authored Agent case with a gold expected trajectory."""

    try:
        expected_trajectory = _parse_step_json(list(args.step or []))
    except ValueError as exc:
        _print({"error": "trajectory_invalid", "detail": str(exc)})
        return 2

    async def body(session: AsyncSession):
        set_id = await repository.resolve_set_id(session, args.set)
        row = await repository.add_case(
            session,
            set_id=set_id,
            task_type=TaskKind.agent,
            input={"question": args.question},
            expected={"answer": args.answer} if args.answer else None,
            tags=list(args.tag or []),
            expected_trajectory=expected_trajectory,
        )
        return _case_to_dict(row)

    return run_db_command(body)


def _cmd_eval_set_show(args: argparse.Namespace) -> int:
    async def body(session: AsyncSession):
        set_id = await repository.resolve_set_id(session, args.set)
        set_row = await repository.get_eval_set(session, set_id)
        cases = await repository.list_cases(session, set_id, statuses=None)
        return {
            **_set_to_dict(set_row),
            "cases": [_case_to_dict(c) for c in cases],
        }

    return run_db_command(body)


def _cmd_run(args: argparse.Namespace) -> int:
    """Run an eval set; `fixed` dumps records, `sequential` renders a GateReport."""
    if getattr(args, "gate_mode", "fixed") == "sequential":
        return _cmd_run_sequential(args)
    from pydantic import ValidationError
    from yaml import YAMLError

    async def body(session: AsyncSession):
        return await eval_runner.run_eval(
            session,
            eval_set_id_or_name=args.eval_set,
            prompt_path=str(args.prompt),
            judge_model_override=args.judge_model,
            k_override=args.k,
            concurrency_override=args.concurrency,
            policy_mode_override=args.policy_mode,
            limit=args.limit,
            mock=True if args.mock else None,
        )

    try:
        result = asyncio.run(_with_session(body))
    except EvalGateError as exc:  # e.g. eval_set_not_found
        _print(exc.payload())
        return exc.exit_code
    except FileNotFoundError as exc:
        _print({"error": "prompt_not_found", "detail": str(exc)})
        return 2
    except (ValidationError, YAMLError, ValueError) as exc:
        _print({"error": "prompt_invalid", "detail": str(exc)})
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    records = [r.model_dump() for r in result.records]
    args.out.write_text(json.dumps({"records": records}, indent=2, default=_json_default))
    _print(
        {
            "run_id": result.run_id,
            "eval_set_id": result.eval_set_id,
            "total_cases": result.total_cases,
            "mean_score": result.mean_score,
        }
    )
    return 0


def _cmd_run_sequential(args: argparse.Namespace) -> int:
    """`evalgate run --gate-mode sequential`: stream against a baseline, stop early.

    Unlike fixed mode, `--out` receives the **GateReport JSON** (per-case records
    still persist to the DB) and the exit code is the gate verdict
    (0 pass / 1 fail / 2 error) — same convention as `evalgate gate` / phase12.
    """
    from pydantic import ValidationError
    from yaml import YAMLError

    from evalgate.gate import sequential as gate_seq

    if not args.baseline_run:
        _print({"error": "missing_baseline", "detail": "--baseline-run is required for sequential"})
        return 2

    async def body(session: AsyncSession):
        return await gate_seq.run_sequential_gate(
            session,
            eval_set=args.eval_set,
            prompt_path=str(args.prompt),
            baseline_run_id=args.baseline_run,
            look_every=args.look_every,
            spending=args.spending,
            mde=args.mde,
            gamma=args.gamma,
            judge_model_override=args.judge_model,
            mock=True if args.mock else None,
        )

    try:
        result = asyncio.run(_with_session(body))
    except EvalGateError as exc:  # eval_set_not_found / sequential_unrunnable
        _print(exc.payload())
        return exc.exit_code
    except FileNotFoundError as exc:
        _print({"error": "prompt_not_found", "detail": str(exc)})
        return 2
    except (ValidationError, YAMLError, ValueError) as exc:
        _print({"error": "prompt_invalid", "detail": str(exc)})
        return 2

    report = result.report
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report.model_dump_json(indent=2))
    _print({"run_id": result.run_id, "passed": report.passed, "summary": report.summary})
    return 0 if report.passed else 1


def _add_run_subcommand(parent: argparse._SubParsersAction) -> None:
    run = parent.add_parser(
        "run",
        help="Run an eval set against a prompt.yaml; emits gate-ready records JSON.",
    )
    run.add_argument("--eval-set", required=True, dest="eval_set", help="set id (UUID) or name")
    run.add_argument("--prompt", required=True, type=Path, help="path to prompt.yaml")
    run.add_argument("--out", required=True, type=Path, help="where to write records JSON")
    run.add_argument(
        "--judge-model",
        default=None,
        dest="judge_model",
        help="Override judges[*].model from the YAML (applied to every entry).",
    )
    run.add_argument(
        "--k",
        type=int,
        default=None,
        help="Override judge_policy.k (self-consistency repeat count).",
    )
    run.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Override judge_policy.concurrency (per-case fan-out limit).",
    )
    run.add_argument(
        "--policy-mode",
        choices=["pointwise", "pairwise"],
        default=None,
        dest="policy_mode",
        help="Override judge_policy.mode.",
    )
    run.add_argument(
        "--mock",
        action="store_true",
        default=False,
        help="Force litellm mock_response on all calls (CI / offline mode).",
    )
    run.add_argument("--limit", type=int, default=None, help="Cap cases evaluated (debug).")
    run.add_argument(
        "--gate-mode",
        choices=["fixed", "sequential"],
        default="fixed",
        dest="gate_mode",
        help=(
            "fixed (default): judge every case, emit records JSON. "
            "sequential: stream against --baseline-run and stop early, emit GateReport JSON."
        ),
    )
    run.add_argument(
        "--baseline-run",
        default=None,
        dest="baseline_run",
        help="[sequential] eval_runs id to pair against (per case_id).",
    )
    run.add_argument(
        "--look-every",
        type=int,
        default=seq_engine.DEFAULT_LOOK_EVERY,
        dest="look_every",
        help="[sequential] interim look cadence in cases (default 5).",
    )
    run.add_argument(
        "--spending",
        choices=["obf", "pocock"],
        default=seq_engine.DEFAULT_SPENDING,
        help="[sequential] alpha-spending function for the early-FAIL boundary.",
    )
    run.add_argument(
        "--mde",
        type=float,
        default=seq_engine.DEFAULT_MDE,
        help="[sequential] min detectable regression (score scale) for curtailment.",
    )
    run.add_argument(
        "--gamma",
        type=float,
        default=seq_engine.DEFAULT_GAMMA,
        help="[sequential] conditional-power threshold for early PASS (default 0.2).",
    )
    run.set_defaults(func=_cmd_run)


_VALID_BADCASE_STRATEGIES = ("uncertainty", "outlier", "llm")


def _cmd_badcase_list(args: argparse.Namespace) -> int:
    calibrator = None
    if getattr(args, "calibration", None):
        calibrator = calibration_repo.load_calibrator(args.calibration)
        if calibrator is None:
            _print({"error": "calibration_not_found", "detail": args.calibration})
            return 2

    async def run(session: AsyncSession):
        items = await badcase_finder.find(
            session,
            strategy=args.strategy,
            run_id=args.run,
            limit=args.limit,
            mock=bool(args.mock),
            calibrator=calibrator,
        )
        return {"strategy": args.strategy, "items": [bc.to_dict() for bc in items]}

    _print(asyncio.run(_with_session(run)))
    return 0


def _cmd_badcase_promote(args: argparse.Namespace) -> int:
    async def body(session: AsyncSession):
        membership = await badcase_repo.promote_result_to_set(
            session,
            eval_result_id=args.result,
            target_set_id_or_name=args.eval_set,
            strategy=args.strategy,
            extra_tags=list(args.tag or []),
        )
        return {
            "id": membership.id,
            "eval_case_id": membership.eval_case_id,
            "eval_set_id": membership.eval_set_id,
            "promoted_from_result_id": membership.promoted_from_result_id,
            "strategy": membership.strategy,
            "tags": list(membership.tags or []),
            "created_at": membership.created_at,
        }

    return run_db_command(body)


def _add_badcase_subcommands(parent: argparse._SubParsersAction) -> None:
    badcase = parent.add_parser(
        "badcase",
        help="Find and promote regression-worthy cases from eval_results.",
    )
    sub = badcase.add_subparsers(dest="badcase_cmd", required=True)

    lst = sub.add_parser("list", help="List badcase candidates by strategy.")
    lst.add_argument(
        "--strategy",
        choices=_VALID_BADCASE_STRATEGIES,
        default="uncertainty",
        help="Which signal to rank by.",
    )
    lst.add_argument(
        "--run",
        default=None,
        help="Restrict to a single eval_run (UUID hex). Default: all runs.",
    )
    lst.add_argument("--limit", type=int, default=20)
    lst.add_argument(
        "--mock",
        action="store_true",
        default=False,
        help="Force litellm mock for the `llm` strategy (CI / offline).",
    )
    lst.add_argument(
        "--calibration",
        default=None,
        help=(
            "[uncertainty] path to calibration_params.json. When given, rank by "
            "calibrated uncertainty (closeness of P(good) to 0.5) instead of raw "
            "judge_confidence (Phase 16)."
        ),
    )
    lst.set_defaults(func=_cmd_badcase_list)

    promote = sub.add_parser(
        "promote", help="Copy a badcase's underlying eval_case into a target eval_set."
    )
    promote.add_argument(
        "--result",
        required=True,
        help="eval_result_id (from `badcase list`).",
    )
    promote.add_argument(
        "--eval-set",
        required=True,
        dest="eval_set",
        help="Target set id (UUID hex) or name.",
    )
    promote.add_argument(
        "--strategy",
        default=None,
        help="Strategy that surfaced this badcase (recorded in tags).",
    )
    promote.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Extra tag to attach to the new case (repeatable).",
    )
    promote.set_defaults(func=_cmd_badcase_promote)


def _cmd_shadow_rollup(args: argparse.Namespace) -> int:
    async def run(session: AsyncSession):
        row = await shadow_rollup.run_rollup(
            session,
            args.candidate_hash,
            window_hours=args.window_hours,
        )
        summary = row.report.get("summary") if isinstance(row.report, dict) else None
        return {
            "id": row.id,
            "candidate_prompt_hash": row.candidate_prompt_hash,
            "window_start": row.window_start,
            "window_end": row.window_end,
            "n_observations": row.n_observations,
            "passed": row.passed,
            "alerted": row.alerted,
            "summary": summary,
        }

    result = asyncio.run(_with_session(run))
    _print(result)
    return 0 if result.get("passed") else 1


def _cmd_shadow_report(args: argparse.Namespace) -> int:
    async def run(session: AsyncSession):
        report, obs, window_start, window_end = await shadow_rollup.compute_live_report(
            session,
            args.candidate_hash,
            window_hours=args.window_hours,
        )
        return {
            "candidate_prompt_hash": args.candidate_hash,
            "window_start": window_start,
            "window_end": window_end,
            "n_observations": len(obs),
            "passed": report.passed,
            "report": report.model_dump(mode="json"),
        }

    result = asyncio.run(_with_session(run))
    _print(result)
    return 0 if result.get("passed") else 1


def _add_shadow_subcommands(parent: argparse._SubParsersAction) -> None:
    shadow = parent.add_parser(
        "shadow",
        help="Roll up production shadow observations into a 4-axis report.",
    )
    sub = shadow.add_subparsers(dest="shadow_cmd", required=True)

    report = sub.add_parser(
        "report",
        help="Compute (read-only) a rolling shadow report for a candidate prompt hash.",
    )
    report.add_argument(
        "--candidate-hash",
        required=True,
        dest="candidate_hash",
        help="candidate_prompt_hash (the SDK's grouping key).",
    )
    report.add_argument("--window-hours", type=int, default=24, dest="window_hours")
    report.set_defaults(func=_cmd_shadow_report)

    rollup = sub.add_parser(
        "rollup",
        help="Compute + persist a shadow report snapshot and alert on regression.",
    )
    rollup.add_argument(
        "--candidate-hash",
        required=True,
        dest="candidate_hash",
        help="candidate_prompt_hash (the SDK's grouping key).",
    )
    rollup.add_argument("--window-hours", type=int, default=24, dest="window_hours")
    rollup.set_defaults(func=_cmd_shadow_rollup)


def _cmd_adversarial_generate(args: argparse.Namespace) -> int:
    use_mock = bool(args.mock) or is_mock_llm()
    model = args.model or get_settings().adversarial_generator_model

    async def body(session: AsyncSession):
        created = await adversarial_repo.generate_into_set(
            session,
            set_id_or_name=args.set,
            tag=args.tag,
            k=args.k,
            model=model,
            mock=use_mock,
        )
        return {
            "tag": args.tag,
            "requested": args.k,
            "created": [_case_to_dict(c) for c in created],
        }

    return run_db_command(body)


def _cmd_adversarial_review(args: argparse.Namespace) -> int:
    if not (args.approve or args.reject):
        # No decision given -> just list pending cases for review.
        async def list_body(session: AsyncSession):
            pending = await adversarial_repo.list_pending(session, set_id_or_name=args.set)
            return {"pending": [_case_to_dict(c) for c in pending]}

        return run_db_command(list_body)

    decision = "approve" if args.approve else "reject"
    case_id = args.approve or args.reject

    async def body(session: AsyncSession):
        row = await adversarial_repo.review_case(session, case_id=case_id, decision=decision)
        return _case_to_dict(row)

    return run_db_command(body)


def _cmd_adversarial_stats(args: argparse.Namespace) -> int:
    async def body(session: AsyncSession):
        result = await adversarial_repo.stats(
            session, set_id_or_name=args.set, threshold=args.threshold
        )
        return result.to_dict()

    return run_db_command(body)


def _add_adversarial_subcommands(parent: argparse._SubParsersAction) -> None:
    adversarial = parent.add_parser(
        "adversarial",
        help="Auto-generate red-team cases for a weak tag and review them (Phase 14).",
    )
    sub = adversarial.add_subparsers(dest="adversarial_cmd", required=True)

    generate = sub.add_parser(
        "generate",
        help="Synthesize K pending adversarial cases for a tag in a set.",
    )
    generate.add_argument("--set", required=True, help="eval set id (UUID hex) or name")
    generate.add_argument("--tag", required=True, help="the weak tag to attack")
    generate.add_argument("--k", type=int, default=10, help="number of cases to generate")
    generate.add_argument(
        "--model",
        default=None,
        help="Override the generator model (default: settings.adversarial_generator_model).",
    )
    generate.add_argument(
        "--mock",
        action="store_true",
        default=False,
        help="Force deterministic offline generation (CI / offline).",
    )
    generate.set_defaults(func=_cmd_adversarial_generate)

    review = sub.add_parser(
        "review",
        help="List pending adversarial cases, or approve/reject one by id.",
    )
    review.add_argument("--set", required=True, help="eval set id (UUID hex) or name")
    review.add_argument("--approve", default=None, help="case id to approve (-> active)")
    review.add_argument("--reject", default=None, help="case id to reject (-> archived)")
    review.set_defaults(func=_cmd_adversarial_review)

    stats = sub.add_parser(
        "stats",
        help="Hit-rate report over approved adversarial cases (hit = score < threshold).",
    )
    stats.add_argument("--set", required=True, help="eval set id (UUID hex) or name")
    stats.add_argument(
        "--threshold",
        type=float,
        default=adversarial_repo.DEFAULT_HIT_THRESHOLD,
        help="absolute score below which a case counts as a hit (default 0.5).",
    )
    stats.set_defaults(func=_cmd_adversarial_stats)


def _cmd_calibration_label(args: argparse.Namespace) -> int:
    async def body(session: AsyncSession):
        row = await calibration_repo.add_label(
            session,
            eval_result_id=args.result,
            label=args.label,
            annotator=args.annotator,
            note=args.note,
        )
        return {
            "id": row.id,
            "eval_result_id": row.eval_result_id,
            "label": row.label,
            "annotator": row.annotator,
        }

    return run_db_command(body)


def _cmd_calibration_fit(args: argparse.Namespace) -> int:
    out = args.out or get_settings().calibration_params_path

    async def body(session: AsyncSession):
        return await calibration_repo.fit_and_save(session, params_path=out, run_id=args.run)

    try:
        report = asyncio.run(_with_session(body))
    except EvalGateError as exc:  # insufficient_labels
        _print(exc.payload())
        return exc.exit_code
    _print(
        {
            "params_path": out,
            "n": report.n,
            "temperature": report.temperature,
            "ece_before": report.ece_before,
            "ece_after": report.ece_after,
            "mce_before": report.mce_before,
            "mce_after": report.mce_after,
        }
    )
    return 0


def _cmd_calibration_report(args: argparse.Namespace) -> int:
    params_path = args.params or get_settings().calibration_params_path
    calibrator = calibration_repo.load_calibrator(params_path)

    async def body(session: AsyncSession):
        return await calibration_repo.compute_report(
            session, calibrator=calibrator, run_id=args.run
        )

    try:
        report, stats = asyncio.run(_with_session(body))
    except EvalGateError as exc:  # insufficient_labels
        _print(exc.payload())
        return exc.exit_code
    if args.plot:
        from evalgate.report.calibration import render_reliability_png

        render_reliability_png(stats, args.plot)
    out = report.model_dump()
    if args.plot:
        out["plot"] = args.plot
    _print(out)
    return 0


def _add_calibration_subcommands(parent: argparse._SubParsersAction) -> None:
    calibration = parent.add_parser(
        "calibration",
        help="Calibrate judge scores against human labels (ECE + temperature scaling, Phase 16).",
    )
    sub = calibration.add_subparsers(dest="calibration_cmd", required=True)

    label = sub.add_parser("label", help="Attach a human good/bad label to an eval_result.")
    label.add_argument("--result", required=True, help="eval_result_id (from `badcase list`).")
    label.add_argument(
        "--label",
        required=True,
        choices=[v.value for v in HumanLabel],
        help="human verdict: good (-> 1) / bad (-> 0).",
    )
    label.add_argument("--annotator", default="human", help="who labeled it (audit).")
    label.add_argument("--note", default=None, help="optional free-text note.")
    label.set_defaults(func=_cmd_calibration_label)

    fit = sub.add_parser(
        "fit",
        help="Fit a temperature on labeled results and write calibration_params.json.",
    )
    fit.add_argument("--run", default=None, help="restrict to one eval_run (default: all).")
    fit.add_argument(
        "--out",
        default=None,
        help="params path (default: settings.calibration_params_path).",
    )
    fit.set_defaults(func=_cmd_calibration_fit)

    report = sub.add_parser(
        "report",
        help="Print ECE/MCE before vs after + optional reliability-diagram PNG.",
    )
    report.add_argument("--run", default=None, help="restrict to one eval_run (default: all).")
    report.add_argument(
        "--params",
        default=None,
        help="params path to apply (default: settings path; ephemeral fit if absent).",
    )
    report.add_argument(
        "--plot",
        default=None,
        help="write a reliability diagram PNG to this path (needs matplotlib).",
    )
    report.set_defaults(func=_cmd_calibration_report)


def _add_eval_set_subcommands(parent: argparse._SubParsersAction) -> None:
    eval_set = parent.add_parser("eval-set", help="Manage eval sets and cases.")
    sub = eval_set.add_subparsers(dest="eval_set_cmd", required=True)

    create = sub.add_parser("create", help="Create a new eval set.")
    create.add_argument("--name", required=True)
    create.add_argument("--description", default=None)
    create.set_defaults(func=_cmd_eval_set_create)

    add = sub.add_parser("add", help="Promote a trace into an eval case.")
    add.add_argument("--set", required=True, help="eval set id (UUID hex) or name")
    add.add_argument("--from-trace", required=True, dest="from_trace")
    add.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Extra tag to attach (repeatable).",
    )
    add.add_argument(
        "--task-type",
        choices=[t.value for t in TaskKind],
        default=None,
        dest="task_type",
        help="Override the heuristic task_type inference.",
    )
    add.set_defaults(func=_cmd_eval_set_add)

    show = sub.add_parser("show", help="Print an eval set and its cases.")
    show.add_argument("--set", required=True)
    show.set_defaults(func=_cmd_eval_set_show)

    add_rag = sub.add_parser(
        "add-rag-case",
        help="Add a hand-authored RAG case (question / answer / reference contexts).",
    )
    add_rag.add_argument("--set", required=True, help="eval set id (UUID hex) or name")
    add_rag.add_argument("--question", required=True)
    add_rag.add_argument("--answer", required=True, help="gold answer (eval_case.expected.answer)")
    add_rag.add_argument(
        "--context",
        action="append",
        default=[],
        help="reference context chunk (repeatable). Stored on eval_case.retrieved_contexts.",
    )
    add_rag.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Tag to attach to the case (repeatable).",
    )
    add_rag.set_defaults(func=_cmd_eval_set_add_rag)

    add_agent = sub.add_parser(
        "add-agent-case",
        help="Add a hand-authored Agent case (question / expected trajectory).",
    )
    add_agent.add_argument("--set", required=True, help="eval set id (UUID hex) or name")
    add_agent.add_argument("--question", required=True)
    add_agent.add_argument(
        "--answer",
        default=None,
        help="Optional gold final answer (eval_case.expected.answer).",
    )
    add_agent.add_argument(
        "--step",
        action="append",
        default=[],
        help=(
            "Expected trajectory step JSON (repeatable), e.g. "
            '\'{"tool":"lookup_invoice","args":{"invoice_id":"INV-42"}}\''
        ),
    )
    add_agent.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Tag to attach to the case (repeatable).",
    )
    add_agent.set_defaults(func=_cmd_eval_set_add_agent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evalgate", description="EvalGate CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    gate = sub.add_parser("gate", help="Run multi-axis CI gate on baseline vs candidate.")
    gate.add_argument("--baseline", type=Path, required=True)
    gate.add_argument("--candidate", type=Path, required=True)
    gate.add_argument("--out", type=Path, default=None, help="Write JSON report to this path.")
    gate.set_defaults(func=_cmd_gate)

    _add_eval_set_subcommands(sub)
    _add_run_subcommand(sub)
    _add_badcase_subcommands(sub)
    _add_adversarial_subcommands(sub)
    _add_calibration_subcommands(sub)
    _add_shadow_subcommands(sub)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
