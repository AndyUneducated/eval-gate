# Contributing to EvalGate

Thanks for your interest in improving EvalGate! This guide covers the local
workflow and the checks CI enforces.

## Development setup

EvalGate uses [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
uv sync                # install core + dev deps (all optional extras)
make db-up             # start local Postgres (docker compose)
uv run alembic upgrade head
```

## Before you open a PR

Run the same gates CI runs:

```bash
make lint       # ruff check + ruff format --check + mypy
make test       # pytest
make coverage   # pytest with coverage report (optional locally)
```

Or the individual pieces:

```bash
uv run ruff check .
uv run ruff format .
uv run mypy
uv run pytest
```

### Guidelines

- **Tests**: add or update tests for any behavior change. Unit tests run
  against in-memory SQLite; Alembic migrations are validated against Postgres
  in CI.
- **Types**: `mypy` must pass. Prefer honest annotations over `# type: ignore`.
- **Style**: `ruff` owns lint + format (line length 100). Don't hand-format.
- **Commits/PRs**: keep them focused; describe the "why". Reference issues.
- **Optional deps**: features that need `ragas` / `presidio` / `streamlit` /
  `matplotlib` must import them lazily so the core install stays lean (see the
  extras in `pyproject.toml`).

## Offline / mock mode

Set `EVALGATE_MOCK_LLM=1` to force fully-offline, deterministic LLM calls — this
is what the smoke scripts and CI gate use. See the `*-smoke` targets in the
`Makefile`.

## Reporting security issues

Please follow `SECURITY.md` — do not open public issues for vulnerabilities.

By contributing you agree that your contributions are licensed under the
project's Apache-2.0 license.
