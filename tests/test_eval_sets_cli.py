"""CLI coverage. We point `evalgate.cli.SessionLocal` at the test aiosqlite
factory so subcommands write to the in-memory DB the rest of the suite uses."""

from __future__ import annotations

import json

import pytest

from evalgate import cli


@pytest.fixture
def patched_session(monkeypatch, db_session_factory):
    """Override the module-level SessionLocal both in cli and in the repository
    chain (repository goes through `persistence.get_trace`, which only uses
    the session it's given, so patching at the cli layer is enough)."""
    monkeypatch.setattr(cli, "SessionLocal", db_session_factory)
    yield db_session_factory


def test_cli_eval_set_create_prints_row(patched_session, capsys) -> None:
    rc = cli.main(["eval-set", "create", "--name", "billing"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["name"] == "billing"
    assert out["id"]


def test_cli_eval_set_show_reports_missing_set(patched_session, capsys) -> None:
    rc = cli.main(["eval-set", "show", "--set", "ghost"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "eval_set_not_found"


def test_cli_eval_set_add_reports_missing_trace(patched_session, capsys) -> None:
    cli.main(["eval-set", "create", "--name", "demo"])
    capsys.readouterr()  # flush

    rc = cli.main(
        [
            "eval-set",
            "add",
            "--set",
            "demo",
            "--from-trace",
            "nonexistent",
        ]
    )
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "trace_not_found"


def test_cli_eval_set_add_end_to_end(patched_session, capsys) -> None:
    """Insert a trace via the repository's underlying tables, then promote
    it through the CLI, then read it back via `show`."""
    import asyncio
    from datetime import datetime, timedelta

    from evalgate.core.schemas import Span
    from evalgate.ingest import persistence

    async def seed():
        base = datetime(2026, 5, 14, 12, 0, 0)
        spans = [
            Span(
                span_id="root",
                trace_id="tcli",
                name="rag-pipeline",
                kind="other",
                start_time=base,
                end_time=base + timedelta(seconds=1),
                attributes={"evalgate.tag": "billing"},
            ),
            Span(
                span_id="llm",
                trace_id="tcli",
                parent_span_id="root",
                name="llm.call",
                kind="other",
                start_time=base + timedelta(milliseconds=20),
                end_time=base + timedelta(milliseconds=900),
                attributes={
                    "gen_ai.system": "openai",
                    "gen_ai.prompt": "what is 2+2?",
                    "gen_ai.response.content": "four",
                },
            ),
        ]
        async with patched_session() as session:
            await persistence.persist_spans(session, spans, {"service.name": "demo-app"})

    asyncio.run(seed())

    assert cli.main(["eval-set", "create", "--name", "demo"]) == 0
    capsys.readouterr()

    rc = cli.main(
        [
            "eval-set",
            "add",
            "--set",
            "demo",
            "--from-trace",
            "tcli",
            "--tag",
            "smoke",
            "--task-type",
            "rag",
        ]
    )
    assert rc == 0
    case = json.loads(capsys.readouterr().out)
    assert case["task_type"] == "rag"
    assert case["input"] == {"prompt": "what is 2+2?"}
    assert case["tags"] == ["billing", "smoke"]
    assert case["source_trace_id"] == "tcli"

    rc = cli.main(["eval-set", "show", "--set", "demo"])
    assert rc == 0
    show = json.loads(capsys.readouterr().out)
    assert show["name"] == "demo"
    assert len(show["cases"]) == 1


def test_cli_eval_set_add_agent_case(patched_session, capsys) -> None:
    assert cli.main(["eval-set", "create", "--name", "agent-demo"]) == 0
    capsys.readouterr()

    rc = cli.main(
        [
            "eval-set",
            "add-agent-case",
            "--set",
            "agent-demo",
            "--question",
            "why invoice is unpaid",
            "--answer",
            "because due date not reached",
            "--step",
            '{"tool":"lookup_invoice","args":{"invoice_id":"INV-42"}}',
            "--step",
            '{"tool":"fetch_policy","args":{"topic":"billing"}}',
            "--tag",
            "agent",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["task_type"] == "agent"
    assert out["expected_trajectory"] == [
        {"tool": "lookup_invoice", "args": {"invoice_id": "INV-42"}},
        {"tool": "fetch_policy", "args": {"topic": "billing"}},
    ]


def test_cli_eval_set_add_agent_case_rejects_bad_step_json(patched_session, capsys) -> None:
    assert cli.main(["eval-set", "create", "--name", "agent-demo-2"]) == 0
    capsys.readouterr()

    rc = cli.main(
        [
            "eval-set",
            "add-agent-case",
            "--set",
            "agent-demo-2",
            "--question",
            "q",
            "--step",
            "{bad-json",
        ]
    )
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["error"] == "trajectory_invalid"
