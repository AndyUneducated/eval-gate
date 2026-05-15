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

from evalgate.core.schemas import TaskKind
from evalgate.db.session import SessionLocal
from evalgate.eval_set import repository
from evalgate.gate.decision import build_gate_report


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
        "eval_set_id": row.eval_set_id,
        "task_type": row.task_type,
        "input": row.input,
        "expected": row.expected,
        "tags": list(row.tags or []),
        "source_trace_id": row.source_trace_id,
        "source_span_id": row.source_span_id,
        "created_at": row.created_at,
    }


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evalgate", description="EvalGate CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    gate = sub.add_parser("gate", help="Run multi-axis CI gate on baseline vs candidate.")
    gate.add_argument("--baseline", type=Path, required=True)
    gate.add_argument("--candidate", type=Path, required=True)
    gate.add_argument("--out", type=Path, default=None, help="Write JSON report to this path.")
    gate.set_defaults(func=_cmd_gate)

    _add_eval_set_subcommands(sub)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
