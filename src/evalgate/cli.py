"""`evalgate` command-line entrypoint.

Today exposes a single subcommand:

    evalgate gate --baseline baseline.json --candidate candidate.json [--out report.json]

The process exits 0 if the gate passes and 1 if any axis regresses with a
statistically significant delta — this is what CI hooks into.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evalgate", description="EvalGate CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    gate = sub.add_parser("gate", help="Run multi-axis CI gate on baseline vs candidate.")
    gate.add_argument("--baseline", type=Path, required=True)
    gate.add_argument("--candidate", type=Path, required=True)
    gate.add_argument("--out", type=Path, default=None, help="Write JSON report to this path.")
    gate.set_defaults(func=_cmd_gate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
