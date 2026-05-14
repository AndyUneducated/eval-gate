# EvalGate

> **Eval-First LLMOps with CI Gate** — turn production LLM traces into a multi-axis
> regression gate that blocks bad PRs from shipping.

[![ci](https://github.com/AndyUneducated/eval-gate/actions/workflows/ci.yml/badge.svg)](https://github.com/AndyUneducated/eval-gate/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![license](https://img.shields.io/badge/license-Apache--2.0-green.svg)](LICENSE)

---

## What it does

EvalGate ingests OpenTelemetry traces from your LLM app, mines **BadCases** via
uncertainty sampling, runs a **task-aware judge** (RAG / Agent / generic) on every PR,
and **blocks merges** when a four-axis gate trips:

- **quality** — pass rate, with bootstrap-CI significance to defeat stochastic-eval noise
- **cost** — token-spend regression
- **latency** — p95 latency regression
- **safety** — PII / jailbreak violation rate

Regressions are attributed by `tag` / `intent` so the report says
*"billing intent dropped 8 pts"* instead of *"pass rate dropped 0.5%"*.

> **Status**: walking skeleton — see [`docs/design.md`](docs/design.md) for the roadmap.

## Quickstart

```bash
# 1. Install uv (https://docs.astral.sh/uv/), then:
uv sync

# 2. Boot Postgres
make db-up

# 3. Run tests
make test
```

## Development

| Command | What it does |
|---|---|
| `make install` | Install all deps (incl. dev tools) into `.venv/` |
| `make dev` | Start local Postgres in Docker |
| `make test` | Run pytest |
| `make lint` | Ruff check + format check |
| `make format` | Auto-fix lint + format |
| `make db-up` / `make db-down` | Manage local Postgres |

## License

Apache-2.0 — see [LICENSE](LICENSE).
