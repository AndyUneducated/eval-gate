# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- API key authentication for all `/v1/*` routes (`EVALGATE_API_KEY`); a no-op
  until a key is configured, so local dev is unchanged.
- `/readyz` readiness probe that checks database connectivity (distinct from the
  liveness-only `/healthz`).
- Request-ID middleware: honours an inbound `X-Request-ID` or mints one, binds
  it to structured logs, and echoes it on the response.
- Configurable CORS allow-list (`EVALGATE_CORS_ALLOW_ORIGINS`) and request body
  size limit (`EVALGATE_MAX_REQUEST_BYTES`, default 25 MiB).
- Optional dependency extras (`rag`, `safety`, `ui`, `viz`, `all`) so the core
  install stays lean; heavy stacks are imported lazily.
- CI now runs `mypy` and coverage, validates Alembic migrations (upgrade +
  downgrade) against a real Postgres, and runs an advisory dependency audit.
- `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, issue/PR templates.

### Changed

- Developer-only seed routes (`/v1/dev/*`) are mounted only when
  `EVALGATE_ENV` is `local`/`dev`/`test`.
- MultiJudge aggregates per-judge confidence with a size-invariant geometric
  mean (was a product that decayed toward 0 as judges were added).
- Judge/candidate LLM calls now carry a default 60s timeout.
- Database engine uses a tuned connection pool and is disposed on shutdown.

### Fixed

- OTLP/JSON ingest now round-trips hex-encoded trace/span ids correctly (were
  mis-decoded as base64), so real OTLP exporters interoperate.
- Malformed OTLP bodies return HTTP 422 instead of a 500 (protobuf
  `DecodeError` / `ParseError` are now handled).
- Transport-failed judge calls are recorded as *no signal* rather than a hard
  `0.0`, so one flaky call can't poison a run's mean/variance.
- Same-model judge ensembles no longer collide into one confidence entry.
- Agent evaluator reports real cost/latency and penalises spurious extra tool
  calls; per-step mismatch reasons map to the right step.
- `evalgate gate` exits `2` (infra error) on missing/malformed inputs instead of
  `1` (regression).
- Adversarial `review` only acts on *pending adversarial* cases (state-machine
  guard); `--approve`/`--reject` are mutually exclusive.
- Tag attribution skips one-sided tags instead of fabricating a `0.0` baseline.
- Trace rollup converges under concurrent partial deliveries and no longer lets
  an empty payload clobber stored `resource_attributes`.
- UI: safety-axis deltas are colored as regressions when rates rise; span names
  are HTML-escaped before rendering.

## [0.1.0]

- Initial public preview: OTLP ingest, eval sets, multi-axis CI gate, judge
  stack (self-consistency, position-swap, multi-judge), RAG/agent evaluators,
  safety pipeline, shadow mode, adversarial synthesis, sequential gate,
  calibration, and agreement tooling.
