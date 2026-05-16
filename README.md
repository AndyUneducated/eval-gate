# EvalGate

> **Eval-First LLMOps with CI Gate** — turn production LLM traces into a multi-axis
> regression gate that blocks bad PRs from shipping.

[![CI](https://github.com/AndyUneducated/eval-gate/actions/workflows/ci.yml/badge.svg)](https://github.com/AndyUneducated/eval-gate/actions/workflows/ci.yml)
[![eval-gate](https://github.com/AndyUneducated/eval-gate/actions/workflows/eval-gate.yml/badge.svg)](https://github.com/AndyUneducated/eval-gate/actions/workflows/eval-gate.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![codecov](https://img.shields.io/badge/coverage-pending-lightgrey.svg)](https://codecov.io)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![repo size](https://img.shields.io/github/repo-size/AndyUneducated/eval-gate)](https://github.com/AndyUneducated/eval-gate)

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/badge/uv-managed-261230.svg?logo=astral&logoColor=white)](https://docs.astral.sh/uv/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Pydantic v2](https://img.shields.io/badge/Pydantic-v2-E92063.svg?logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-D71F00.svg?logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![Alembic](https://img.shields.io/badge/Alembic-migrations-6BA539.svg)](https://alembic.sqlalchemy.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1.svg?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-425CC7.svg?logo=opentelemetry&logoColor=white)](https://opentelemetry.io/)
[![LiteLLM](https://img.shields.io/badge/LiteLLM-multi--provider-8A2BE2.svg)](https://github.com/BerriAI/litellm)
[![Ragas](https://img.shields.io/badge/Ragas-judges-7B61FF.svg)](https://docs.ragas.io/)
[![Presidio](https://img.shields.io/badge/Presidio-PII-1E90FF.svg?logo=microsoft&logoColor=white)](https://microsoft.github.io/presidio/)
[![Streamlit](https://img.shields.io/badge/Streamlit-ops_UI-FF4B4B.svg?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![pytest](https://img.shields.io/badge/tests-pytest-0A9EDC.svg?logo=pytest&logoColor=white)](https://docs.pytest.org/)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen.svg?logo=pre-commit)](https://pre-commit.com/)

---

## Why EvalGate

LLM PRs ship with a single number on the wall — *"pass rate dropped 0.5%, looks fine"* — and that number is wrong four ways at once. A real CI gate has to reject regressions on **quality, cost, latency, and safety** simultaneously, with enough statistical rigor to survive stochastic judges, and enough attribution to point at the offending intent or tag.

| What the PR author wants to know | What you actually need to answer it | Why a naive eval pass-rate gate fails |
|---|---|---|
| *"Did answer quality regress?"* | bootstrap-CI on pass rate, per task | Stochastic LLM judges drift 1–3 pts on identical inputs; naive deltas trip on noise and miss real regressions. |
| *"Did this PR get more expensive?"* | per-tag / per-intent token-spend deltas | A flat *"+5% tokens"* hides *"+50% on billing intent, free elsewhere"* — exactly the regression you wanted caught. |
| *"Will users feel a slowdown?"* | p95 latency, not mean | p50 stays flat while the tail blows up; users feel the tail. |
| *"Did we open a new safety hole?"* | four sub-axes: PII in / PII leak out, jailbreak attempt / jailbreak comply | One *"violation rate"* number conflates *"users tried to jailbreak"* (input) with *"the model complied"* (output) — opposite signals, opposite fixes. |
| *"Is this regression real or just noise?"* | bootstrap CI + significance flag per axis | Without significance, every PR is either green-by-luck or red-by-luck and the gate gets disabled within a week. |
| *"Where did it regress?"* | tag / intent attribution table on every report | Aggregate numbers don't route to an owner; per-tag rows do. |

EvalGate routes each axis to the right statistic and reports them in the same PR comment, so the gate decision is a fact, not an opinion.

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

## Contributing

PRs are welcome — especially around new judge tasks, additional gate axes, and adapters for non-OTel trace sources.

1. **Set up.** Install [`uv`](https://docs.astral.sh/uv/), then `uv sync` (installs runtime + the `dev` group: pytest, ruff, pre-commit, OTel SDK).
2. **Boot Postgres locally.** `make db-up` (Docker Compose); apply migrations with `uv run alembic upgrade head`.
3. **Run the checks before pushing.** `make lint` (ruff check + format check) and `make test` (pytest, async-mode auto). Optional: `pre-commit install` to wire the same checks into git.
4. **Schema changes** go through Alembic — `uv run alembic revision --autogenerate -m "<msg>"` against a clean DB, then commit the migration alongside the model change.
5. **Document load-bearing decisions** in [`DECISIONS.md`](DECISIONS.md) (ADR-style); shipped phases in [`JOURNAL.md`](JOURNAL.md); product-level changes in [`docs/design.md`](docs/design.md).
6. **CI gates every PR** with both `ci.yml` (lint + tests) and `eval-gate.yml` (multi-axis regression gate); the gate must pass for merge.

For larger proposals (new gate axis, breaking API change), open an issue first.

## License

Apache-2.0 — see [LICENSE](LICENSE).
