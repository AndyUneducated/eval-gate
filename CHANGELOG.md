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
- Deduplicated shared helpers: one `new_id()` (was five identical `_new_id`),
  one `safe_completion_cost()` / `stringify()` (was copy-pasted per module),
  one `loads_tolerant_json()` (was duplicated in the jailbreak classifier and
  adversarial synthesizer), one `clamp_score()` (RAG evaluator dropped its local
  `_clamp`), and one `_dialect_name()` in the ingest persistence layer.
- Trace-rollup aggregate read is a single grouped query (was one round-trip per
  trace_id on the ingest hot path).
- Calibration `compute_report` fetches group keys once instead of twice for
  conditional (non-global) scopes.
- README gained a module map, a deep usage walkthrough (prompt.yaml anatomy,
  full CLI lifecycle, shadow SDK), a REST API reference, and a key-algorithm
  index.

### Fixed

- Cases that fail to evaluate (`error=True` — unsupported task, missing
  reference, runner failure, or every judge call failing) are now excluded from
  every gate axis and tag attribution, so an infra failure can't be counted as a
  quality-0 regression and false-block a PR. When every judge fails on a case,
  the generic evaluator flags it as an error rather than emitting a real `0.0`.
- Sequential gate skips errored candidate records instead of feeding their
  placeholder `0.0` diff into the alpha-spending boundary (could trip an early
  FAIL).
- UI Reports: the safety axis now reads per-case values from
  `axis_breakdown.safety` (any-violation flag) instead of falling through to the
  quality score, so safety attribution/coloring is correct.
- API key comparison is constant-time (`hmac.compare_digest`), closing a timing
  side-channel.
- OTLP ingest enforces the body-size cap on the raw read, so a chunked /
  `Content-Length`-less upload can't bypass the middleware memory-DoS guard.
- `/readyz` no longer echoes raw database exception text to unauthenticated
  callers (logged server-side instead).
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
- Safety pipeline fails fast at build time when `presidio-analyzer` is missing
  (was swallowed per-case, silently reporting a bogus 0% PII / "clean" signal
  for the whole run).
- OTLP/JSON ingest returns HTTP 422 for a non-object JSON body (bare scalar /
  array) instead of an uncaught 500.
- Domain-error responses include the stable machine-readable `error` slug
  (`{"error": ..., "detail": ...}`), matching the CLI's JSON error shape.
- CORS no longer enables credentials against a wildcard origin (would reflect
  cookies/`Authorization` back to any site).
- Empty/whitespace `output`/`answer` reference fields are treated as "no
  reference" rather than a blank reference string.
- Mock RAG pseudo-embeddings now vary across all vector dimensions (the previous
  hash reused only 8 distinct components, collapsing cosine geometry).

## [0.1.0]

- Initial public preview: OTLP ingest, eval sets, multi-axis CI gate, judge
  stack (self-consistency, position-swap, multi-judge), RAG/agent evaluators,
  safety pipeline, shadow mode, adversarial synthesis, sequential gate,
  calibration, and agreement tooling.
