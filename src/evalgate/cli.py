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

from evalgate.badcase import finder as badcase_finder
from evalgate.badcase import repository as badcase_repo
from evalgate.core.schemas import TaskKind
from evalgate.db.session import SessionLocal
from evalgate.eval_set import repository
from evalgate.evaluator import runner as eval_runner
from evalgate.gate.decision import build_gate_report
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
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _case_to_dict(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "task_type": row.task_type,
        "input": row.input,
        "expected": row.expected,
        "tags": list(row.tags or []),
        "retrieved_contexts": list(row.retrieved_contexts or []),
        "expected_trajectory": list(row.expected_trajectory or []),
        "source_trace_id": row.source_trace_id,
        "source_span_id": row.source_span_id,
        "created_at": row.created_at,
    }


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


def _cmd_eval_set_create(args: argparse.Namespace) -> int:
    async def run(session: AsyncSession):
        row = await repository.create_eval_set(
            session, name=args.name, description=args.description
        )
        return _set_to_dict(row)

    _print(asyncio.run(_with_session(run)))
    return 0


def _cmd_eval_set_add(args: argparse.Namespace) -> int:
    task_type = TaskKind(args.task_type) if args.task_type else None

    async def run(session: AsyncSession):
        try:
            set_id = await repository.resolve_set_id(session, args.set)
            row = await repository.add_case_from_trace(
                session,
                set_id=set_id,
                trace_id=args.from_trace,
                extra_tags=list(args.tag or []),
                task_type_override=task_type,
            )
            return _case_to_dict(row)
        except repository.EvalSetNotFoundError as exc:
            return {"error": "eval_set_not_found", "detail": str(exc)}
        except repository.TraceNotFoundError as exc:
            return {"error": "trace_not_found", "detail": str(exc)}
        except repository.NoLLMSpanError as exc:
            return {"error": "no_llm_span", "detail": str(exc)}

    result = asyncio.run(_with_session(run))
    _print(result)
    return 1 if isinstance(result, dict) and "error" in result else 0


def _cmd_eval_set_add_rag(args: argparse.Namespace) -> int:
    """Add a hand-authored RAG case (question + gold answer + reference contexts).

    Used by the Phase 8 demo seeder; for traces, ``eval-set add --from-trace``
    still applies and now also carries any contexts ``case_extract`` recovers.
    """

    async def run(session: AsyncSession):
        try:
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
        except repository.EvalSetNotFoundError as exc:
            return {"error": "eval_set_not_found", "detail": str(exc)}

    result = asyncio.run(_with_session(run))
    _print(result)
    return 1 if isinstance(result, dict) and "error" in result else 0


def _cmd_eval_set_add_agent(args: argparse.Namespace) -> int:
    """Add a hand-authored Agent case with a gold expected trajectory."""

    try:
        expected_trajectory = _parse_step_json(list(args.step or []))
    except ValueError as exc:
        _print({"error": "trajectory_invalid", "detail": str(exc)})
        return 2

    async def run(session: AsyncSession):
        try:
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
        except repository.EvalSetNotFoundError as exc:
            return {"error": "eval_set_not_found", "detail": str(exc)}

    result = asyncio.run(_with_session(run))
    _print(result)
    return 1 if isinstance(result, dict) and "error" in result else 0


def _cmd_eval_set_show(args: argparse.Namespace) -> int:
    async def run(session: AsyncSession):
        try:
            set_id = await repository.resolve_set_id(session, args.set)
        except repository.EvalSetNotFoundError as exc:
            return {"error": "eval_set_not_found", "detail": str(exc)}
        set_row = await repository.get_eval_set(session, set_id)
        cases = await repository.list_cases(session, set_id)
        return {
            **_set_to_dict(set_row),
            "cases": [_case_to_dict(c) for c in cases],
        }

    result = asyncio.run(_with_session(run))
    _print(result)
    return 1 if isinstance(result, dict) and "error" in result else 0


def _cmd_run(args: argparse.Namespace) -> int:
    """Run an eval set through (candidate LLM -> RubricJudge) and dump records."""
    from pydantic import ValidationError
    from yaml import YAMLError

    async def run(session: AsyncSession):
        try:
            result = await eval_runner.run_eval(
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
        except repository.EvalSetNotFoundError as exc:
            return {"_status": 1, "error": "eval_set_not_found", "detail": str(exc)}
        except FileNotFoundError as exc:
            return {"_status": 2, "error": "prompt_not_found", "detail": str(exc)}
        except (ValidationError, YAMLError, ValueError) as exc:
            return {"_status": 2, "error": "prompt_invalid", "detail": str(exc)}
        return {
            "_status": 0,
            "run_id": result.run_id,
            "eval_set_id": result.eval_set_id,
            "total_cases": result.total_cases,
            "mean_score": result.mean_score,
            "records": [r.model_dump() for r in result.records],
        }

    payload = asyncio.run(_with_session(run))
    status = payload.pop("_status")
    if status == 0:
        records = payload.pop("records")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"records": records}, indent=2, default=_json_default))
    _print(payload)
    return status


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
    run.set_defaults(func=_cmd_run)


_VALID_BADCASE_STRATEGIES = ("uncertainty", "outlier", "llm")


def _cmd_badcase_list(args: argparse.Namespace) -> int:
    async def run(session: AsyncSession):
        items = await badcase_finder.find(
            session,
            strategy=args.strategy,
            run_id=args.run,
            limit=args.limit,
            mock=bool(args.mock),
        )
        return {"strategy": args.strategy, "items": [bc.to_dict() for bc in items]}

    _print(asyncio.run(_with_session(run)))
    return 0


def _cmd_badcase_promote(args: argparse.Namespace) -> int:
    async def run(session: AsyncSession):
        try:
            membership = await badcase_repo.promote_result_to_set(
                session,
                eval_result_id=args.result,
                target_set_id_or_name=args.eval_set,
                strategy=args.strategy,
                extra_tags=list(args.tag or []),
            )
        except badcase_repo.BadCaseNotFoundError as exc:
            return {"_status": 1, "error": "badcase_not_found", "detail": str(exc)}
        except badcase_repo.AlreadyPromotedError as exc:
            return {"_status": 1, "error": "already_promoted", "detail": str(exc)}
        except repository.EvalSetNotFoundError as exc:
            return {"_status": 1, "error": "eval_set_not_found", "detail": str(exc)}
        return {
            "_status": 0,
            "id": membership.id,
            "eval_case_id": membership.eval_case_id,
            "eval_set_id": membership.eval_set_id,
            "promoted_from_result_id": membership.promoted_from_result_id,
            "strategy": membership.strategy,
            "tags": list(membership.tags or []),
            "created_at": membership.created_at,
        }

    payload = asyncio.run(_with_session(run))
    status_code = payload.pop("_status")
    _print(payload)
    return status_code


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
    _add_shadow_subcommands(sub)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
