# DECISIONS · Core technical decision log

> This file records EvalGate technical decisions that **actually affect architecture, direction, or long-term maintainability**.
> It does not record pure preferences such as "ruff instead of black"; it records decisions a future engineer would ask "why didn't we do it that other way?"
>
> Format follows ADR (Architecture Decision Record) loosely — four essential parts: **Context / Decision / Rationale / Consequences**.
>
> Once written, a decision is **never deleted or rewritten**; if we reverse course, **add a new entry** with `Status: superseded by ADR-N` so the reasoning trail stays.
>
> Numbers increase monotonically. New decisions are appended at the end.

## ADR index

| # | Decision | Status | One-liner |
|---|---|---|---|
| **ADR-001** | OTel as the trace protocol | accepted | Open standard for zero-migration apps and no vendor lock-in |
| **ADR-002** | Postgres + JSONB | accepted | Schema-less fields in JSONB, keeping flexibility and SQL |
| **ADR-003** | Cut the Prompt management UI | accepted | Prompts as git-managed config; focus on evaluation |
| **ADR-004** | Four-axis + significance + attribution gate | accepted (Phase 2) | Covers misses, false blocks, and unexplained failures |
| **ADR-005** | Task-tiered evaluators + multi-judge debiasing | accepted (Phase 5/6/8/9) | Lower variance and bias; cover RAG / Agent / generic |
| **ADR-006** | Streamlit for UI | accepted | Ops-oriented dashboard; spend time on the backend |
| **ADR-007** | `uv` for package management | accepted | Fast, single binary, PEP 621 compatible |
| **ADR-008** | LiteLLM for unified LLM calls | accepted (Phase 5) | One interface for 100+ providers; enables cross-vote |
| **ADR-009** | CI gate with mock judge + ephemeral SQLite | accepted (Phase 12) | Offline, deterministic, zero-cost CI; real models via `make ci-gate-real` |
| **ADR-010** | Shadow Mode: SDK-side scoring + on-demand rollup | accepted (Phase 13) | Thin backend; reuse `EvalRecord` + `build_gate_report`; no scheduler dependency |
| **ADR-011** | Case status/source lifecycle + reference-free adversarial cases | accepted (Phase 14) | Pending never enters the gate via `list_cases` default active-only; hit = absolute threshold; red-team cases have no gold |
| **ADR-012** | Sequential gate: paired sequential test + α-spending / curtailment, quality only | accepted (Phase 15) | Early-FAIL via Lan-DeMets α-spending, early-PASS via stochastic curtailment; sequential on quality only, other axes fixed-N snapshots; sequential decision is authoritative |
| **ADR-013** | Judge Calibration: calibrate score (not confidence) + DB human-label table + read-time Calibrator | accepted (Phase 16) | Temperature scaling turns `score` into a true probability; labels in `human_labels` (reused for P17 κ); read-time transform keeps `eval_results` immutable |
| **ADR-014** | Cohen's κ reuses `human_labels` + thresholded decisions | accepted (Phase 17) | κ measures judge vs human after chance agreement; binarize at `score ≥ 0.5`; no new table/migration |
| **ADR-015** | p95 significance: smoothed + sample-size-guarded bootstrap (pays ADR-004 debt) | accepted (Phase 17) | Silverman smoothing for tail-quantile discreteness; `min_reliable_n` guard against small-sample false-blocks |
| **ADR-016** | Conditional calibration: per-`task_type` / per-`judge_model` temperatures + read-time group T | accepted (Phase 17, implements ADR-013 reserved shape) | Global T as fallback + per-group fits; thin groups fall back to global; no extra `eval_results` columns |
| **ADR-017** | Cloud deploy: ECS Fargate + RDS, Terraform stack, GitHub OIDC publish | accepted (Phase 18) | Serverless containers, simpler than EKS; single public subnet skips NAT (demo tradeoff); OIDC avoids long-lived keys; multi-stage non-root image |

> Reading order: each decision expands as **Context → Decision → Rationale → Consequences**.

---

## ADR-001 · Use OpenTelemetry as the trace protocol; do not build our own SDK

**Date**: 2026-05-14 · **Status**: accepted

**Context**: Peer products (LangSmith, early Langfuse) ship proprietary SDKs for trace ingest so they can attach richer metadata and a smoother DX. OpenTelemetry / OTLP is the more open industry standard; an app can instrument with a single instrumentor.

**Decision**: All trace ingest goes over OTLP (HTTP / gRPC). EvalGate does not provide, and does not plan to provide, its own SDK.

**Rationale**:
1. App-side integration cost is the main adoption factor for B2B tools. OTel plus `opentelemetry-instrumentation-openai` is enough to report; a proprietary SDK would require changing business code.
2. **Avoiding vendor lock-in is the selling point enterprise buyers care about most.** Switching backends later (Datadog / Honeycomb / Phoenix) has zero migration cost.
3. The open ecosystem (`openinference` / `openllmetry`) already covers most LLM-specific semantic conventions; ride that rather than invent a new one.

**Consequences**:
- Need a mapper from OTel attributes to the internal `traces` + `spans` model (already in `src/evalgate/ingest/otel_mapper.py`).
- Lose fine-grained control of SDK UX; missing edge-case fields wait on upstream or our own PRs.
- The ingest path must absorb "future unknown attributes," which is why we store them in JSONB (see ADR-002).

---

## ADR-002 · Postgres + JSONB rather than NoSQL (Mongo / DynamoDB)

**Date**: 2026-05-14 · **Status**: accepted

**Context**: OTel span `attributes` are schema-less key-value and sit awkwardly in a classic RDBMS; NoSQL is a natural fit. EvalGate's core queries, though, are "aggregate by tag," "p95 over a time window," and "join eval_run × eval_case" — SQL strengths.

**Decision**:
- Primary store is **Postgres**.
- Unfixed-schema fields (OTel attributes, judge raw output, tool args) live in **JSONB** columns.
- Schema evolution uses explicit **Alembic** migrations.

**Rationale**:
1. JSONB is first-class on Postgres: GIN indexes, `->`, `->>`, `@>` all work well.
2. The team (one person) is far more fluent in SQL than Mongo; bootstrap speed wins.
3. A single Postgres instance can handle tens of millions of trace rows; moving to ClickHouse at that scale is still timely (the trace table is cold-write / hot-read and easy to migrate).
4. Managed Postgres on RDS is first-class AWS support; Phase 13 deploy cost stays controllable.

**Consequences**:
- High-throughput OTLP ingest needs async + batch insert (FastAPI + asyncpg + `COPY` or multi-row insert).
- If trace volume later hits 10^9, move to columnar store (ClickHouse) or hot/cold split (PG hot + S3 + Athena cold). Add a new ADR then.

---

## ADR-003 · Cut the Prompt management UI; prompts as config files (git-native)

**Date**: 2026-05-14 · **Status**: accepted

**Context**: LangSmith / PromptLayer both ship heavy prompt hubs (version diffs, A/B, UI editors). Looks complete — should we follow?

**Decision**: **Do not** build a prompt management UI. Prompts are committed in the app repo as YAML / Python modules; git versions them. EvalGate only evaluates a given prompt.

**Rationale**:
1. This is a red ocean: 5+ OSS tools already do it; another copy has zero differentiation.
2. UI work would roughly double, with zero differentiation — better to deepen the evaluator.
3. **Reinforces an eval-first position**: we do not replace the prompt toolchain; we are the QA gate on prompt changes.
4. Prompt-as-code integrates naturally with PRs, code review, and git blame — a more engineering-native shape.

**Consequences**:
- Apps choose their own prompt file format (YAML / Jinja / Python module). We provide an example schema but do not hard-constrain it.
- We lose the "non-engineers editing prompts" audience (PMs / labelers); they were never the target users.

---

## ADR-004 · The CI gate is four axes + bootstrap CI significance + tag attribution, not a single pass rate

**Date**: 2026-05-14 · **Status**: accepted (Phase 2 shipped v1)

**Context**: Default OSS eval tools fail when "pass rate drops below a threshold." In production that gate has three known failure modes:
- Misses: pass rate holds while cost doubles / latency p95 doubles / safety violations rise.
- False blocks: LLM eval is stochastic; 92% → 89% may be noise. One false block and everyone `--force`s the gate next time — **the whole system is dead**.
- No diagnosis: "pass rate dropped 3%" is an alarm, not a root cause; developers still have to dig traces.

**Decision**: The CI gate requires three pieces —
1. **Multi-axis**: quality / cost / latency_p95 / safety in parallel; any axis regression fails.
2. **Statistical significance**: mean-like axes use a **bootstrap CI (1000 resamples, 95%)**; a true regression requires the CI not to cross 0. p95-like axes in v1 use a threshold first (the interpretation of resampled p95 is subtle; revisit in Phase 17).
3. **Tag attribution**: every case is tagged; on failure report which tag cluster dropped, not only a global number.

**Rationale**:
1. Multi-axis covers misses, significance covers false blocks, tag attribution covers unexplained failures — missing any one is a demo, not a product.
2. Bootstrap is less sensitive to distribution shape than a paired t-test; eval scores are often non-normal (bimodal or truncated), so bootstrap is more stable.
3. The gate has to be something developers keep, not something they bypass — that is a precondition for the product.

**Consequences**:
- Bootstrap cost is O(N × resamples); hundreds of eval cases × 1000 resamples is milliseconds, negligible vs judge calls.
- Tag maintenance is pushed to the app (manual or semi-automatic tags on prompts / cases).
- p95 significance is technical debt, to be revisited in Phase 17.

---

## ADR-005 · Task-tiered evaluators + multi-judge cross-vote + position-swap + self-consistency

**Date**: 2026-05-14 · **Status**: accepted (Phase 5/6/8/9 to land)

**Context**: Pure LLM-as-Judge (one model, one call, generic rubric) is already baseline in 2026, with at least three known defects:
- Single-call variance ±15% (same input scored differently across 3 runs).
- Task heterogeneity: RAG cares about citation faithfulness, Agent about action sequences, generic about answer quality; one rubric necessarily distorts.
- Known biases (Zheng 2023 MT-Bench): position bias / verbosity bias / self-preference bias.

**Decision**: Four-piece stack —
1. **Task-tiered evaluators**: RAG → RAGAS; Agent → trajectory eval (tool-call accuracy + step-wise success); generic → rubric LLM-as-Judge. `EvaluatorRouter` dispatches on `eval_case.task_type`.
2. **Multi-judge cross-vote**: cross-family (GPT-4 + Claude) to fight self-preference bias.
3. **Debias wrappers**: position-swap (swap A/B twice and require agreement) + verbosity normalization (normalize by length).
4. **Self-consistency**: judge each case K=3 times, majority vote + confidence.

**Rationale**: Single-judge variance and bias are consensus in papers and industry; without a fix, "significance" is noise-dominated. Task tiering is a fundamental constraint on evaluator quality — without it, RAG and Agent sharing a rubric are both inaccurate.

**Consequences**:
- **Eval cost ×6–10** (multi-model × multi-call). Accepted on purpose because CI-gate trustworthiness is the product. Production can add caching / sampling to bring cost back to ×2–3.
- Complexity jumps — extra layers like `MultiJudge` / `PositionSwapJudge` / `EvaluatorRouter`. Phase 6 needs a dedicated reproduction script proving variance actually drops from ±15% to ±3% (otherwise this decision does not stand).

---

## ADR-006 · Streamlit for UI, not React/Next.js

**Date**: 2026-05-14 · **Status**: accepted

**Context**: As an ops / data-display platform, UI is required; a full React stack has high ramp-up cost, and strategy is backend / eval algorithms.

**Decision**: UI is a single **Streamlit** container; no frontend/backend split.

**Rationale**:
1. Streamlit is 5–10× faster than React for ops dashboards.
2. The audience (ML engineers / DevOps) is not picky about interaction; they need to see the data clearly.
3. Front-end time saved goes into evaluator algorithms and cloud deploy — those are the resume-worthy pieces.
4. If SaaS / multi-tenant demand appears later, switch to Next.js + a standalone backend; by then the data API is already REST and the frontend is swappable.

**Consequences**:
- UI cannot do highly custom interaction (drag-and-drop, complex forms); this project's scenarios do not need them.
- Streamlit session state is somewhat unintuitive; page-to-page state should travel via query params.

---

## ADR-007 · `uv` for Python package management / venv

**Date**: 2026-05-14 · **Status**: accepted

**Context**: Python packaging in 2024–2026 is in a generational shift — pip / poetry / pdm / rye / uv are all options.

**Decision**: Use **uv** (`uv sync`, `uv run`, `uv lock`).

**Rationale**:
1. 10–100× faster than poetry; CI time drops materially.
2. Single binary, zero Python bootstrap dependency (no need for a Python already installed to install the package manager).
3. Compatible with PEP 621 `pyproject.toml`, so switching tools later is cheap.

**Consequences**:
- Teammates must install uv (CI already uses `astral-sh/setup-uv@v3`).
- uv is still evolving quickly; occasional breaking updates mean watching release notes.

---

## ADR-008 · LiteLLM as the unified LLM call layer

**Date**: 2026-05-14 · **Status**: accepted (Phase 5 to introduce)

**Context**: Judges need multiple cross-family models (GPT-4 + Claude + possibly Gemini). Writing each vendor SDK once inflates code and makes cross-vote hard to abstract.

**Decision**: All external LLM calls go through **LiteLLM** (unified `completion()` interface).

**Rationale**:
1. One interface, 100+ providers; adding / switching models is zero cost.
2. Built-in retry / fallback / cost tracking; no need to write our own.
3. CI can use LiteLLM mock / record-replay instead of burning API quota.
4. Directly supports ADR-005 multi-judge cross-vote.

**Consequences**:
- Extra abstraction layer; rare provider-specific features (e.g. Anthropic prompt caching) need a bypass.
- Depends on LiteLLM's maintenance pace — it is very active, not a problem today.

---

## ADR-009 · CI gate uses mock judge + ephemeral SQLite; real models go through an explicit manual entry point

**Date**: 2026-06-11 · **Status**: accepted (Phase 12)

**Context**: Phase 12 replaced the CI gate's static fixtures with a real judge pipeline (seed reference set → run baseline prompt → run candidate prompt → diff gate). Running real LLMs on GitHub Actions has three problems: (1) burns tokens / requires API keys in CI secrets; (2) judges are stochastic, so gate conclusions jitter across PRs and are hard to reproduce; (3) `evalgate run` writes a DB, so CI would need a Postgres service. Most PRs in this repo are unrelated to prompt quality (docs, ingest code, …); scoring them with a real model is both expensive and produces meaningless "regression" noise.

**Decision**:
- The CI `eval-gate` workflow runs `EVALGATE_MOCK_LLM=1` — judge / candidate / ragas all go through LiteLLM mock: offline, deterministic, zero cost.
- Semantics of this CI step: **end-to-end connectivity smoke**. Assert every `task_type` produces a non-error record; the gate report includes four axes + RAG/agent quality sub-items + safety sub-items; under mock, baseline and candidate share the same set and every axis agrees → the gate must pass.
- Real-model eval uses an explicit manual entry: `make ci-gate-real` (local Ollama) or `workflow_dispatch` with mock off.
- The orchestrator (`scripts/phase12_ci_gate.py`) uses **ephemeral SQLite** in CI (`Base.metadata.create_all`, no alembic) and does not depend on a Postgres service.

**Rationale**:
1. **CI should test "the pipeline is not broken," not "is this PR's prompt good"** — the latter only matters after a consumer repo adopts EvalGate, on their own prompt PRs. Splitting the two keeps CI stable.
2. Mock determinism means the gate does not randomly go red / green from judge jitter, so the team will not disable the gate after a false block (exactly the failure mode ADR-004 wants to avoid).
3. Zero tokens, no CI secrets, smaller security surface.
4. Ephemeral SQLite makes the CI job stateless and free of external deps, isomorphic with each phase's smoke scripts (same dialect-agnostic repository path; see ADR-002).

**Consequences**:
- CI does not automatically catch real quality regressions — that happens after a consumer repo integrates, on their prompt PRs, or locally via `make ci-gate-real`. The exit criterion "worse prompt → CI fail + attribution" is reproduced locally with a real model (~140s measured).
- The mock judge always returns 0.5, so this CI step cannot verify correctness of the significance decision itself — that is covered by unit tests of `report/significance.py` and Phase 17 reproduction experiments.
- Running a real model in CI requires a self-hosted runner + model, with mock removed via `workflow_dispatch`.

---

## ADR-010 · Shadow Mode scores on the SDK side; rollup is on-demand, not an in-process scheduler

**Date**: 2026-06-11 · **Status**: accepted (Phase 13)

**Context**: Shadow Mode must evaluate a candidate harmlessly on production traffic. Two unavoidable design forks: (1) production has no human ground truth — where do primary / candidate scores come from, and who computes them? (2) "Every hour, roll a 4-axis report and alert" is a periodic job — should the service embed a timer / background worker?

**Decision**:
- **Scoring lives in the client SDK**: after `evalgate.shadow(...)` hits sampling, a background task runs the candidate concurrently and reuses `build_judge_stack(primary)` so primary / candidate are scored **with the same rubric**, reference-free. Results pack into two `EvalRecord`s and POST to `/v1/shadow/observe`. The backend only writes observations and aggregates by `candidate_prompt_hash`; it does not run judges.
- **Rolling reports are on-demand + explicit rollup**: `GET /v1/shadow/reports` computes the 4 axes for the window in real time (not persisted); `POST /v1/shadow/rollup` (and `evalgate shadow rollup` CLI) persists a `shadow_reports` snapshot and fires alerts. Production cron calls rollup; the service itself has no built-in timer.

**Rationale**:
1. **Thin backend = max reuse**: the observe payload is the `EvalRecord` contract already frozen for Phase 13 (see comments in `core/schemas.py`). Rolling aggregation feeds `gate.decision.build_gate_report` — shadow and PR CI **share** four axes + bootstrap CI + tag attribution + `axis_breakdown` sub-axes, with zero new stats code.
2. **Scoring naturally lives at the call site**: the SDK already holds `PromptSpec` and a LiteLLM channel to run the candidate; scoring in place avoids a round-trip of "send both outputs back to the backend and judge again," and avoids standing up a judge worker on the backend that can reach prompt config.
3. **No scheduler dependency**: introducing APScheduler / a resident task in a 1-person-day phase is over-engineering. Cron calling an idempotent CLI matches the git-native / config-external tone (echoes ADR-003), and `compute_shadow_report` is a pure function, easy to test.
4. **Never block the hot path**: fire-and-forget + 1s timeout + swallow exceptions. Shadow being slow or down must not affect production requests — that is the precondition for shipping shadow.

**Consequences**:
- Scoring uses the primary's judge stack: if the candidate should be judged with a stricter rubric, that needs an explicit extension (kept simple on purpose so both sides stay comparable).
- On-demand rollup means "how often we roll" is an ops choice (cron frequency); the service does not guarantee real-time; alert latency = rollup period.
- The SDK brings LiteLLM judge calls into the caller process: cost/latency sit on the background task (not the hot path), but the caller still pays those judge tokens.
- Background tasks need a strong-ref set (`_BACKGROUND_TASKS`) against GC — a known asyncio fire-and-forget pitfall, already encapsulated.
- Alerts are a homemade Slack-compatible webhook (`{"text": ...}` + log fallback when URL is unset); no Slack SDK. Rich text / multi-channel later.

---

## ADR-011 · Case lifecycle (status/source) in the data-access layer + reference-free adversarial generation + hit as an absolute threshold

**Date**: 2026-06-12 · **Status**: accepted (Phase 14)

**Context**: Phase 14 red-team auto-generation must feed generator-LLM cases into the eval set, but **unreviewed cases must never enter the gate** (otherwise the flywheel pollutes itself). Three design forks: (1) Where is the "pending cases do not participate in eval" safety invariant — a check in the runner, or lower? (2) Should adversarial cases also generate gold `expected`? (3) How is a "hit" (candidate was stumped) defined — relative drop vs absolute threshold?

**Decision**:
- **Add `status` (pending/active/archived) + `source` (trace/manual/adversarial) to `EvalCaseRow`; sink the safety invariant into the data-access layer**: refactor `eval_set.repository.list_cases` with `statuses` filtering, **default `("active",)`**. Runner / gate read cases via `list_cases`, so they see only active with zero call-site changes; display paths (GET detail / CLI show) pass `statuses=None` explicitly.
- **Adversarial cases are reference-free**: generate only `input`, not gold `expected`; the judge scores with reference-free pointwise.
- **Hit = absolute threshold**: candidate's latest score `< 0.5` (`stats --threshold` is tunable), not "relative drop ≥ X vs some baseline."

**Rationale**:
1. **Invariant in a single data layer > special cases at every caller**: runner, a future sequential gate, and any consumer of `list_cases` automatically get "pending does not enter the gate"; nobody can forget the check. The narrowest data entry point is the most stable place for the rule.
2. **`source` makes provenance observable**: the three states trace/manual/adversarial let attribution and later analysis distinguish human-written vs red-team vs production-harvested; the migration backfills `source='trace'` to preserve history.
3. **Reference-free skips a second human review and matches red-team nature**: red-team value is exposing weaknesses, not supplying gold answers. Generating gold too would require reviewing those answers (LLM-generated answers are untrustworthy) — negative ROI. The judge uses the existing reference-free pointwise path; zero new code.
4. **Absolute threshold is comparable across runs with no baseline-selection bias**: a relative drop needs a "baseline run," and that choice is itself a noise source. Absolute threshold has stable semantics and is immediately explainable ("score below 0.5 means stumped").
5. **No backward-compat baggage**: the project is still being built; changing the `list_cases` signature is cleaner than a parallel function (echoes this round's "clean design > backward compatibility").

**Consequences**:
- `list_cases` signature changed (added `statuses` keyword): every call site either takes the default active-only (runner / gate / most) or passes `None` explicitly (display) — all sites reviewed.
- Mock judge always returns 0.5, so "score < 0.5" never fires under mock: phase14 smoke's top deterministic assertion therefore uses a **safety-axis regression** (admitted injection cases give the candidate an attack surface); true hits are left to real mode + unit tests. Known cost of flattened mock scores.
- Absolute 0.5 is a magic number: "stumped" may differ by task, so it is `--threshold`-tunable, but default reasonableness depends on judge calibration (revisit if Phase 16 does calibration).
- Generation is best-effort throughout (synth never throws → may produce fewer than k cases): that keeps red-team generation from breaking the flywheel, but callers must tolerate "asked for 10, got 7."

---

## ADR-012 · Sequential gate: paired sequential test + α-spending (early-FAIL) / stochastic curtailment (early-PASS), quality axis only

**Date**: 2026-06-12 · **Status**: accepted (Phase 15)

**Context**: A fixed-N gate must finish all N cases before deciding; judge calls are expensive, and many PRs are already "obviously good/bad" mid-run. "Score as we go, stop when evidence is enough" has several design forks: (1) which test — keep the fixed-N two-sample bootstrap, or switch to a paired test? (2) how to set the early-FAIL boundary without inflating cumulative Type-I? (3) what mechanism for early-PASS (beta-spending / simple heuristic / stochastic curtailment)? (4) which axes does the sequential decision cover?

**Decision**:
- **Paired parametric test**: baseline and candidate run the same ordered case set, paired by `case_id`, one-sided test on differences `d_i`; the statistic is mapped to B-value scale (independent-increment Brownian motion under H0).
- **Early-FAIL via Lan-DeMets α-spending** (`obf` default / `pocock`); Armitage-McPherson-Rowe grid recursion for the lower boundary; cumulative α=0.05.
- **Early-PASS via stochastic curtailment**: if conditional power (probability of crossing the boundary by t=1 under the worst tolerable regression drift) < γ → futile → PASS.
- **Sequential only on the quality axis**; cost/latency/safety take a fixed-N snapshot of consumed cases at the stop point. The quality-axis decision is sequential (authoritative); `passed = sequential==PASS ∧ all non-quality axes pass`.
- Self-implemented `norm_cdf` (`math.erf`) / `norm_ppf` (Acklam) — the environment has numpy, not scipy.

**Rationale**:
1. **Paired is more powerful than two-sample and naturally incremental**: the same cases pair 1:1, removing case-difficulty variance; the paired-t statistic's Brownian approximation supports "independent update per new case," while bootstrap is neither incremental nor paired — the right tool for sequential.
2. **α-spending is the standard answer for looking often without inflating Type-I**: each look spends a small increment of the α budget, totaling exactly 0.05. OBF spends almost none early (strictest, most stable); Pocock spends more evenly (slightly more aggressive early).
3. **Curtailment is cleaner than beta-spending**: curtailment only shortens the run and never triggers FAIL, so Type-I is completely unaffected by the PASS boundary — it decouples "save calls" from "control false FAIL." Beta-spending couples to the α boundary and is heavier to implement and argue.
4. **Sequential-only-on-quality is the cost/value optimum**: every judge call is what drives the quality score; all call-saving leverage is there. Cost/latency/safety are cheap to compute, not worth sequential machinery; a stop-point snapshot plus existing `build_gate_report` keeps numbers consistent with the fixed-N gate.

**Consequences**:
- Exit criteria are proven by Monte Carlo (1000 runs / scenario): Type-I ≤ ~0.05, power ≥ 0.8, call savings ≥ 50% — these are load-bearing tests, not decoration.
- **Small-sample footnote**: the normal approximation is slightly aggressive for small-n t statistics; Pocock front-loads α onto the earliest looks, so at n=5 measured Type-I is ~0.08 (slightly over 0.05). Default recommendation is therefore OBF; Pocock's Type-I tests use a realistic first-look gap of n=10. Known cost of approximating a t distribution with a normal boundary.
- Mock judge always returns 0.5 (zero variance), so the statistical demo cannot run: phase15 smoke uses **offline synthetic** (seeded normals) instead of mock LLM — the same honest tradeoff as Phase 14.
- No migration (baseline reuses existing `eval_results`), but baseline and candidate must run the same batch of cases from the same eval set; cases missing a baseline score are silently excluded from pairing.

---

## ADR-013 · Judge Calibration: calibrate `score` (not `judge_confidence`) + human labels in a DB table + read-time Calibrator

**Date**: 2026-06-12 · **Status**: accepted (Phase 16)

**Context**: We want judge output to be readable as a probability — a judge saying 0.8 should mean roughly 80% human pass rate. Three design forks: (1) which quantity to calibrate — `score` or heuristic `judge_confidence`? (2) where to store human labels (ground truth) — JSON file or DB table? (3) where to apply calibration — persist a calibrated score at eval time, or transform at read time?

**Decision**:
- **Calibrate `score`**: single-parameter temperature scaling `p = sigmoid(logit(score)/T)`, minimize logistic NLL in `w=1/T` (convex, golden-section search, no scipy/sklearn). Do not calibrate `judge_confidence`.
- **Human labels live in a new `human_labels` table** (migration 0014, soft reference to `eval_result_id`, no FK), not a JSON file.
- **Apply at read time**: a pure `Calibrator` reads T from `calibration_params.json` and transforms raw scores; `eval_results.score` / `judge_confidence` stay immutable; no runner changes, no extra result columns.

**Rationale**:
1. **`score` is the target signal**: `judge_confidence` ([multi_judge.py](src/evalgate/judge/multi_judge.py) L68-74) is only a heuristic variance proxy and never claimed to be a probability; "judge says 0.8 = 80% pass rate" is about score. Calibrating a quantity that is not a probability is meaningless.
2. **DB table > JSON file**: labels can join `eval_results`, filter by run, and be queried, matching the existing persistence model. More importantly, the table is **also the data source for Phase 17 Cohen's κ (judge vs human agreement)** — one table feeds two phases, avoiding a second label store. Soft references (no FK) let labels survive result/run deletes, following the `eval_results.eval_case_id` convention.
3. **Read-time transform > persist-at-eval**: continues the Phase 14/15 "store raw, transform on read" principle — raw scores are immutable; the calibration curve can be refit / replaced without re-running judges; zero runner changes; no column ambiguity of "which is raw, which is calibrated."
4. **Temperature scaling rather than Platt/isotonic**: one parameter, convex, minimal labels required — the standard reliability-calibration baseline (Guo et al. 2017), matching "human labels are expensive, use as few as possible."

**Consequences**:
- Temperature scaling is a **monotonic** transform: it does not reorder `|score-0.5|`, so BadCase value is **replacing** the `judge_confidence` heuristic ranking (uncorrelated with true ambiguity), not reordering raw scores. Bad-case recall comparison is therefore "calibrated uncertainty vs heuristic confidence." Easy to misread; already noted in plan / smoke.
- Fitting needs a human-label loop (`calibration label`); on degenerate labels (single class / n<10) `fit_temperature` falls back to T=1 (identity) and the CLI exits 2.
- Currently a **single global T**; the params JSON shape reserves room for per-task-type / per-judge curves, not yet implemented.
- New matplotlib dependency (reliability diagram only, Agg lazy-loaded; pure stats path does not trigger it).
- Mock judge always returns 0.5 (zero information), so the calibration demo cannot run: phase16 smoke uses **offline synthetic** overconfident pairs — same honest tradeoff as Phase 14/15.

---

## ADR-014 · Cohen's κ (judge vs human agreement) reuses `human_labels` + thresholded decisions

**Date**: 2026-07-16 · **Status**: accepted (Phase 17)

**Context**: One of the design doc's top talking points is "judge κ vs human ~0.85, approaching the double-human ceiling." Turning that into a runnable number needs an agreement metric between a binary judge decision and a binary human label. Three forks: (1) which metric — raw accuracy or κ? (2) where labels come from — new storage or reuse? (3) how does the judge's "decision" come from continuous `score`?

**Decision**:
- **Use Cohen's κ**: `κ = (p_o − p_e)/(1 − p_e)`, closed-form 2×2 + bootstrap CI (resample pairs 1000 times), numpy only (no sklearn), in the pure engine [report/agreement.py](src/evalgate/report/agreement.py).
- **Reuse the `human_labels` table**: `fetch_scored_labels` returns `(score, label)` directly; zero new tables, zero migrations.
- **Judge decision = `score ≥ threshold`** (default 0.5, `--threshold` tunable).
- Optional `--scope task_type|judge_model` reuses conditional grouping (ADR-016) for per-slice κ.

**Rationale**:
1. **κ subtracts chance agreement**: when both judge and human tend to say good, raw accuracy is inflated by the majority class; κ honestly answers "can the judge replace a human." That is the same ruler used in the literature for double-human κ ~0.85–0.90.
2. **One table, two phases was decided in ADR-013**: `human_labels` was designed for both calibration and κ (soft refs, joinable, filterable by run). This entry cashes that in; no second label store.
3. **Decision threshold = gate pass semantics**: the gate already defines pass as "score above the line"; κ using the same line is most coherent. The threshold is tunable for different tasks' notion of "good."

**Consequences**:
- κ depends on the decision threshold (default 0.5 matches gate semantics, but is not universally optimal).
- Mock judge always returns 0.5 (zero information), so the κ demo cannot run: phase17 smoke uses **offline synthetic** (seeded, noisy human) — same honest tradeoff as Phase 14/15/16.
- On degenerate labels (single class) κ is undefined; by convention return 1 if complete agreement else 0 (unit-tested).

---

## ADR-015 · p95 significance revisit: smoothed + sample-size-guarded bootstrap (pays ADR-004 debt)

**Date**: 2026-07-16 · **Status**: accepted (Phase 17)

**Context**: ADR-004 said the p95 axis would "use a threshold in v1 (interpretation of resampled p95 is subtle; revisit in Phase 17)" and left "p95 significance as technical debt." Technically, a raw nonparametric bootstrap of a high quantile just reshuffles those 1–2 tail order statistics → discrete CIs, low coverage, worse on small samples.

**Decision**: Add two switches to `bootstrap_diff_ci` in [report/significance.py](src/evalgate/report/significance.py), enabled for the `latency_p95` axis:
- **Smoothed bootstrap (`smooth=True`)**: each resample adds `N(0, h²)` kernel noise, Silverman bandwidth `h = 0.9·σ·n^(−1/5)`, so the discrete empirical CDF is smeared continuous.
- **Reliability guard (`min_reliable_n`, 20 for the gate)**: below the threshold mark `reliable=False` and force `significant=False` — a thin-tailed axis **never false-blocks**.
- `BootstrapResult` gains `reliable` / `n_effective`; mean axes stay default (`smooth=False, min_reliable_n=1`). The 10% relative tolerance band remains as belt-and-suspenders.

**Rationale**:
1. **Smoothing fixes the quantile, the guard fixes small n** — the two standard literature patches for "quantile bootstrap is unstable"; simpler than a studentized bootstrap (no need to estimate the variance of the variance).
2. **The guard aligns directly with ADR-004's original intent**: one reason the CI gate exists is "do not false-block"; an axis with too-thin tail data would rather pass than wrongly fail a PR.
3. **Defaults unchanged = zero regression**: mean axes (quality/cost) stay byte-for-byte; the change is spec-limited to the p95 axis.

**Consequences**:
- Smoothing bandwidth is a rule of thumb and widens the CI (conservative) — conservatism is the direction the gate wants.
- Eval sets with fewer than 20 cases per side can no longer call the latency axis significant (blocked by `reliable=False`); intentional "no conclusion without enough data."
- ADR-004's p95 debt is thereby closed.

---

## ADR-016 · Conditional calibration: per-`task_type` / per-`judge_model` temperatures + read-time group T selection

**Date**: 2026-07-16 · **Status**: accepted (Phase 17, implements the extension slot reserved in ADR-013)

**Context**: ADR-013 shipped a **single global T** and explicitly said "the params JSON shape reserves room for per-task-type / per-judge curves, not yet implemented." Judges are often overconfident on one task class and well-calibrated on another; a single T leaves ECE between groups.

**Decision**:
- `Calibrator` generalizes to a temperature family with `scope` + `group_temperatures`: `transform(score, group)` picks T by group; unseen/thin groups fall back to global T; `scope="global"` is byte-for-byte Phase 16.
- Fit global T on all data first (fallback), then a curve per group with enough data (reuse `n ≥ 10` + both classes); thin groups get no independent curve.
- **Group keys joined at read time**: `task_type ← eval_cases`, `judge_model ← eval_runs`; no extra `eval_results` columns.
- `fit --scope` / `report --scope` / `badcase` pass through; params JSON adds `scope` + `groups` (empty ≡ old file).

**Rationale**:
1. **Heterogeneity mainly comes from `task_type` and `judge_model`**, which is also the granularity labels can support; a (task×judge) Cartesian product would thin each cell below what we can fit, so two independent axes + global fallback.
2. **Continue "store raw, transform on read" (ADR-012/013)**: no result columns; curves can change scope and refit anytime; zero runner changes.
3. **Strict backward compatibility**: old global params files, old `Calibrator(temperature=T)` calls, and the global badcase path all stay — new behavior is purely opt-in.

**Consequences**:
- Badcase read path adds one or two `IN (...)` queries for group keys (negligible vs judge calls).
- When labels are sparse, most groups fall back to global T, equivalent to Phase 16 (honest degradation, not a bug).
- Params JSON shape changed (added `scope`/`groups`), but `Calibrator.from_dict` accepts both old and new shapes.

---

## ADR-017 · Cloud deploy: ECS Fargate + RDS, Terraform stack, GitHub OIDC publish

**Date**: 2026-07-16 · **Status**: accepted (Phase 18)

**Context**: The design doc has long described deploy as "Docker Compose (demo) / AWS ECS + RDS (production demo)," but only Compose landed. The cloud half is an explicit resume gap ("learn Cloud"); it needs to become a real stack you can `terraform apply` and publish from CI in one shot. Forks: which container orchestration (ECS vs EKS vs bare EC2), how to lay out the network (cheap vs textbook private), how to publish images (long-lived keys vs OIDC), where migrations run (on service start vs a one-shot task).

**Decision**:
- **Orchestration = ECS Fargate** (serverless containers). The stack is **Terraform**: VPC + 2 AZ public subnets, ALB (`/healthz` health check), ECS service/task definition, RDS Postgres 16, Secrets Manager, IAM. See [deploy/terraform/](deploy/terraform/).
- **Network is a single public-subnet layer**: Fargate tasks get a public IP (SG allows only the ALB); RDS `publicly_accessible=false` + SG allows only the app — **deliberately no NAT gateway** (saves ~$32/mo).
- **Multi-stage image + non-root + HEALTHCHECK**: builder installs deps, runtime copies only venv/source, runs as `appuser` (uid 10001), `docker-entrypoint.sh` splits `serve`/`migrate`.
- **Publish via GitHub OIDC**: `.github/workflows/deploy.yml` assumes an IAM role via OIDC (zero long-lived AK/SK), build→push ECR→`update-service --force-new-deployment`. OIDC provider/role is optionally created in Terraform behind `create_github_oidc`.
- **Migrations default to running on service-task start** (`RUN_MIGRATIONS=true`, `desired_count=1` has no race); when `desired_count>1` use a one-shot `migrate` ECS task (`deploy/scripts/run_migrations.sh`).
- **Secret injection**: DATABASE_URL is assembled in Terraform from the RDS endpoint + a random password, stored in Secrets Manager, injected into ECS as `secrets` — plaintext DSN never lands in the task definition / state outputs; a judge provider key secret is created only if supplied.

**Rationale**:
1. **Fargate is an order of magnitude simpler than EKS**: no node/control-plane ops, billed per task, matches "single-service demo + explainable on a resume." ECS is also first-class AWS (same "managed first" thinking as ADR-002 choosing RDS).
2. **Skipping NAT is a clear-eyed demo tradeoff**: textbook is app/RDS in private subnets + NAT for egress, but NAT is always-on billing and adds no real security for a single-service demo (SG already locks inbound to ALB/app). README states production should move to private + NAT or VPC endpoints.
3. **OIDC is the post-2024 standard for CI→cloud**: the repo stores no long-lived AWS keys; the role trust policy is scoped with `sub = repo:owner/repo:*`; permissions are narrowed to ECR push + ECS publish + PassRole.
4. **Multi-stage non-root image** is container-security baseline; `/healthz` (already exists) feeds container HEALTHCHECK, ALB target group, and ECS container healthCheck — one probe, three consumers.
5. **Migrations on task start** make "apply then ready" work (no race at one task); multi-task uses a one-shot task — both paths exist, chosen by `desired_count`.

**Consequences**:
- The production image still includes heavy deps the API runtime does not need (streamlit/matplotlib/ragas/presidio — existing single dependency set, no extras split) — larger image, known cost / later optional-deps slim-down.
- Public-subnet cheap layout is not the production end state; README/ADR already note the upgrade path to private + NAT.
- ALB currently only `:80` (HTTP); TLS needs an ACM cert + domain, left as a next step (variable slots already sketched).
- Terraform state defaults to local; README comments a backend switch to S3 + DynamoDB lock; real use needs remote state first.
- Not applied against a real AWS account this round (no cloud credentials, avoid spinning up cost); offline verification goes through `terraform validate` + clean `fmt` + image/compose/script syntax checks. A real apply is what incurs cost.
