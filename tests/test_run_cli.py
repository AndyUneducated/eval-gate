"""`evalgate run` CLI end-to-end on aiosqlite + mock mode.

Asserts the `--out` file is exactly the shape `evalgate gate --baseline ...
--candidate ...` consumes — closing the Phase 5 loop.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evalgate import cli
from evalgate.eval_set import repository as set_repo

_PROMPT = """
name: t
candidate:
  model: ollama/qwen3.5:9b
  user_template: "{prompt}"
  params: {}
judges:
  - model: ollama/qwen3.5:9b
    rubric: "rate 0..1 strict json"
    params: {}
judge_policy:
  mode: pointwise
  k: 1
  position_swap: false
  concurrency: 2
"""


@pytest.fixture
def prompt_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "p.yaml"
    p.write_text(_PROMPT)
    return p


@pytest.fixture
def patched_session(monkeypatch, db_session_factory):
    monkeypatch.setattr(cli, "SessionLocal", db_session_factory)
    yield db_session_factory


async def _seed(session_factory, n: int = 3):
    async with session_factory() as session:
        s = await set_repo.create_eval_set(session, name="cli-demo")
        for i in range(n):
            await set_repo.add_case(
                session,
                set_id=s.id,
                input={"prompt": f"q{i}"},
                tags=["billing"],
            )


def test_run_cli_writes_records_consumable_by_gate(patched_session, prompt_yaml, tmp_path, capsys):
    import asyncio

    asyncio.run(_seed(patched_session, n=3))

    baseline = tmp_path / "baseline.json"
    rc = cli.main(
        [
            "run",
            "--eval-set",
            "cli-demo",
            "--prompt",
            str(prompt_yaml),
            "--out",
            str(baseline),
            "--mock",
        ]
    )
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["total_cases"] == 3
    assert summary["mean_score"] == pytest.approx(0.5)

    payload = json.loads(baseline.read_text())
    assert isinstance(payload["records"], list)
    assert len(payload["records"]) == 3
    rec = payload["records"][0]
    for key in ("case_id", "tags", "score", "cost_usd", "latency_ms"):
        assert key in rec

    # Re-feed the JSON straight into `evalgate gate`. Same file twice = zero
    # delta = pass — proves the schema contract holds end-to-end.
    candidate = tmp_path / "candidate.json"
    candidate.write_text(baseline.read_text())
    rc2 = cli.main(
        [
            "gate",
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
        ]
    )
    assert rc2 == 0


def test_run_cli_reports_missing_eval_set(patched_session, prompt_yaml, tmp_path, capsys):
    rc = cli.main(
        [
            "run",
            "--eval-set",
            "ghost",
            "--prompt",
            str(prompt_yaml),
            "--out",
            str(tmp_path / "x.json"),
            "--mock",
        ]
    )
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "eval_set_not_found"


def test_run_cli_reports_invalid_prompt(patched_session, tmp_path, capsys):
    import asyncio

    asyncio.run(_seed(patched_session, n=1))
    capsys.readouterr()
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: a valid spec\n")
    rc = cli.main(
        [
            "run",
            "--eval-set",
            "cli-demo",
            "--prompt",
            str(bad),
            "--out",
            str(tmp_path / "x.json"),
            "--mock",
        ]
    )
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "prompt_invalid"
