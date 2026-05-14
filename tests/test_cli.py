from __future__ import annotations

import json
from pathlib import Path

import pytest

from evalgate.cli import main


def _write(tmp_path: Path, name: str, records: list[dict]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"records": records}))
    return path


def test_cli_passes_on_identical_inputs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    records = [
        {"case_id": f"c{i}", "tags": ["qa"], "score": 0.9, "cost_usd": 0.01, "latency_ms": 1000}
        for i in range(20)
    ]
    base = _write(tmp_path, "baseline.json", records)
    cand = _write(tmp_path, "candidate.json", records)
    out = tmp_path / "report.json"
    rc = main(["gate", "--baseline", str(base), "--candidate", str(cand), "--out", str(out)])
    assert rc == 0
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["passed"] is True


def test_cli_fails_on_regression(tmp_path: Path) -> None:
    base_records = [
        {"case_id": f"c{i}", "tags": ["qa"], "score": 0.9, "cost_usd": 0.01, "latency_ms": 1000}
        for i in range(30)
    ]
    cand_records = [{**r, "score": 0.6} for r in base_records]
    base = _write(tmp_path, "baseline.json", base_records)
    cand = _write(tmp_path, "candidate.json", cand_records)
    rc = main(["gate", "--baseline", str(base), "--candidate", str(cand)])
    assert rc == 1


def test_cli_supports_bare_list_input(tmp_path: Path) -> None:
    records = [
        {"case_id": "c0", "tags": ["qa"], "score": 0.9, "cost_usd": 0.01, "latency_ms": 1000}
    ]
    base = tmp_path / "baseline.json"
    base.write_text(json.dumps(records))
    cand = tmp_path / "candidate.json"
    cand.write_text(json.dumps(records))
    rc = main(["gate", "--baseline", str(base), "--candidate", str(cand)])
    assert rc == 0
