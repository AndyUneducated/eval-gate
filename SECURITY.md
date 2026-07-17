# Security Policy

## Supported versions

EvalGate is pre-1.0 (`0.x`). Security fixes land on `main` and the latest
released `0.x` tag. Older tags are not patched.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately via GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
("Report a vulnerability" under the repository's **Security** tab), or email the
maintainers listed in `pyproject.toml`.

Include, where possible:

- affected version / commit,
- a description and impact assessment,
- reproduction steps or a proof of concept,
- any suggested remediation.

We aim to acknowledge reports within **3 business days** and to ship a fix or
mitigation for confirmed high-severity issues within **30 days**. We'll credit
reporters in the release notes unless you ask us not to.

## Deploying EvalGate securely

EvalGate ingests untrusted traces and calls out to LLM providers. When running
it anywhere other than a local dev box:

- **Set `EVALGATE_API_KEY`.** With no key configured the `/v1/*` API is
  unauthenticated (the local-dev default). In any shared/deployed environment,
  set the key and pass it as `Authorization: Bearer <key>` or `X-API-Key`.
- **Keep `EVALGATE_ENV` out of `local`/`dev`/`test`** in production so the
  developer-only seed routes (`/v1/dev/*`) are not mounted.
- **Front the service with TLS** (load balancer / reverse proxy).
- **Restrict CORS** via `EVALGATE_CORS_ALLOW_ORIGINS` (empty by default).
- **Tune `EVALGATE_MAX_REQUEST_BYTES`** for your ingest volume; the default
  caps request bodies at 25 MiB to bound memory use.
- **Scope provider API keys** (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) to the
  minimum needed, and store them in a secrets manager — never in the image.
- The gate/judge stack executes model output as *data*, never as code, but
  treat all ingested span content and model output as untrusted input.
