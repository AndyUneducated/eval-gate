# Shadow Mode — 3-line integration

Shadow Mode evaluates a candidate prompt harmlessly on **production traffic**: the primary call returns to the user as usual. Sampled requests also run the candidate **in the background** (output is **not** returned to the user). The same judge scores both sides, then results are fire-and-forget posted back to EvalGate. If the candidate regresses significantly on any axis inside the rolling window, an alert fires — catching "unknown unknowns" the PR eval set does not cover.

Full design: [docs/PHASE_13_PLAN.md](./PHASE_13_PLAN.md).

## Integration (3 lines)

```python
from evalgate.shadow import shadow

answer = await shadow(case_input, primary=primary_spec, candidate=candidate_spec)
```

- `case_input`: a `dict`, the input you feed the primary prompt (e.g. `{"question": "..."}`).
- `primary` / `candidate`: `PromptSpec`, loaded from your committed prompt YAML via `evalgate.judge.prompt_spec.load_prompt_spec("path.yaml")`.
- The return value is the **primary** text — return it to the user as-is. Candidate run / scoring / report all happen in the background and **never block or throw** into the hot path.

Optional parameters: `sample_rate` (default `0.1`), `tags` (attribution tags), `case_id`, `client` (custom `ShadowClient`), `mock` (force litellm mock; default reads `EVALGATE_MOCK_LLM`).

Full example: [examples/shadow_demo/app.py](../examples/shadow_demo/app.py).

## Configuration

| Environment variable | Purpose | Default |
|---|---|---|
| `EVALGATE_API_URL` | EvalGate base URL for observe POSTs | `http://localhost:8000` |
| `EVALGATE_SHADOW_WEBHOOK_URL` | Slack-compatible incoming webhook for regression alerts | If unset, degrades to a log warning |

## Backend / ops

| Endpoint | Purpose |
|---|---|
| `POST /v1/shadow/observe` | Accept one scored `(primary, candidate)` `EvalRecord` pair, write `shadow_observations` (returns 202). |
| `GET /v1/shadow/reports?candidate_prompt_hash=&window_hours=24` | Compute the 4-axis rolling report for the window in real time (not persisted). |
| `POST /v1/shadow/rollup?candidate_prompt_hash=&window_hours=24` | Compute + persist a `shadow_reports` snapshot; alert on regression. |

CLI (direct DB, no HTTP):

```bash
evalgate shadow report --candidate-hash <hash>                 # view only
evalgate shadow rollup --candidate-hash <hash> --window-hours 24  # persist snapshot + alert; exit 1 on regression
```

In production, cron `evalgate shadow rollup` (or `POST /v1/shadow/rollup`) on a schedule — this project does not ship a built-in timer.

## Report semantics

The rolling report reuses **exactly the same** `build_gate_report` as the PR CI gate: primary records in the window as baseline, candidate records as candidate. It emits the four axes `quality` / `cost` / `latency_p95` / `safety` (including bootstrap CI, tag attribution, and `axis_breakdown` sub-axes). A significant rise on a lower-is-better axis such as `cost` is a fail.
