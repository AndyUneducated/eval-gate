# EvalGate

> **Eval-First LLMOps with CI Gate** — turn production LLM traces into a multi-axis
> regression gate that blocks bad PRs from shipping.

[![ci](https://github.com/AndyUneducated/eval-gate/actions/workflows/ci.yml/badge.svg)](https://github.com/AndyUneducated/eval-gate/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![license](https://img.shields.io/badge/license-Apache--2.0-green.svg)](LICENSE)
[![repo size](https://img.shields.io/github/repo-size/AndyUneducated/eval-gate)](https://github.com/AndyUneducated/eval-gate)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-D71F00?logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-000000?logo=opentelemetry&logoColor=white)](https://opentelemetry.io/)

---

## What it does

EvalGate ingests OpenTelemetry traces from your LLM app, mines **BadCases** via
uncertainty sampling, runs a **task-aware judge** (RAG / Agent / generic) on every PR,
and **blocks merges** when a four-axis gate trips:

- **quality** — pass rate, with bootstrap-CI significance to defeat stochastic-eval noise
- **cost** — token-spend regression
- **latency** — p95 latency regression
- **safety** — PII (Presidio) and jailbreak (keyword + LLM-classifier) violation rates, broken out into four sub-axes (`pii_input_rate` / `pii_output_leak_rate` / `jailbreak_attempt_rate` / `jailbreak_compliance_rate`)

Regressions are attributed by `tag` / `intent` so the report says
*"billing intent dropped 8 pts"* instead of *"pass rate dropped 0.5%"*.

> **Status**: multi-axis CI gate v1 shipped (fixtures-driven). Real OTel ingest + judge runner up next.

## Project docs

| File | What's in it |
|---|---|
| [`docs/design.md`](docs/design.md) | Long-form product + tech spec — single source of truth for features, architecture, trade-offs. Read this first. |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Phased execution plan (~1 person-day per phase). Tracks `[DONE]` / `[NEXT]` / `[TODO]`. |
| [`DECISIONS.md`](DECISIONS.md) | ADR-style log of every load-bearing technical decision (why OTel, why PG+JSONB, why kill prompt UI, ...). |
| [`JOURNAL.md`](JOURNAL.md) | Reverse-chrono milestone log — one paragraph per shipped phase. |

## Quickstart

```bash
# 1. Install uv (https://docs.astral.sh/uv/), then:
uv sync

# 2. Boot Postgres
make db-up

# 3. Run tests
make test

# 4. Try the multi-axis gate against demo fixtures
uv run python scripts/seed_demo.py
uv run evalgate gate \
  --baseline examples/fixtures/baseline.json \
  --candidate examples/fixtures/candidate.json
# exit 0 = gate passed, exit 1 = regression detected (used by CI)
```

## CI gate

The `eval-gate` workflow runs on every PR: it seeds demo eval records, calls
`evalgate gate`, uploads the JSON report as an artifact, comments the four-axis
table on the PR, and fails the check if any axis regresses with a
statistically significant delta. Swap the seeded fixtures for real
baseline / candidate eval outputs to wire the gate against your own pipeline.

## Development

| Command | What it does |
|---|---|
| `make install` | Install all deps (incl. dev tools) into `.venv/` |
| `make dev` | Start local Postgres in Docker |
| `make test` | Run pytest |
| `make lint` | Ruff check + format check |
| `make format` | Auto-fix lint + format |
| `make db-up` / `make db-down` | Manage local Postgres |
| `make ui` | Start the Streamlit ops UI on `http://127.0.0.1:8501` (talks to `evalgate-api` over HTTP) |

## Ops UI (Phase 11)

A read-only Streamlit UI lives at `src/evalgate/ui/`. It talks to the FastAPI
backend over `/v1/*` only (never directly to the DB), so it stays a real
consumer of the same REST surface as CLI / CI.

```bash
make db-up                      # start Postgres
uv run alembic upgrade head     # apply migrations
uv run python scripts/seed_demo.py
uv run evalgate-api             # in one shell — port 8000
make ui                         # in another — port 8501, opens in browser
```

Three pages:

1. **Traces** — paginated list + span tree detail; "Promote to eval set" button
   wraps `POST /v1/eval-sets/{id}/cases/from-trace/{trace_id}`.
2. **Eval Sets** — create new sets; pick one to inspect its cases.
3. **Reports** — pick an eval set, two `eval_runs` (baseline / candidate),
   render the four-axis gate verdict + sub-axes (RAG / safety) + tag
   attribution.

Configure the API base URL with `EVALGATE_API_URL` (default `http://127.0.0.1:8000`).

## License

Apache-2.0 — see [LICENSE](LICENSE).
