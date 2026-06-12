# Shadow Mode — 3 行接入

Shadow Mode 让 candidate prompt 在**生产流量**上被无害评测：主调用（primary）正常返回给用户，命中采样的请求会在**后台**并发跑一遍 candidate（输出**不**返回给用户），用同一套 judge 给两边打分，再把结果 fire-and-forget 推回 EvalGate。滚动窗口里若 candidate 任一轴显著变差就报警——提前发现 PR eval set 覆盖不到的 "unknown unknown"。

完整设计见 [docs/PHASE_13_PLAN.md](./PHASE_13_PLAN.md)。

## 接入（3 行）

```python
from evalgate.shadow import shadow

answer = await shadow(case_input, primary=primary_spec, candidate=candidate_spec)
```

- `case_input`：`dict`，即你喂给主 prompt 的输入（如 `{"question": "..."}`）。
- `primary` / `candidate`：`PromptSpec`，用 `evalgate.judge.prompt_spec.load_prompt_spec("path.yaml")` 从你 committed 的 prompt YAML 加载。
- 返回值是 **primary** 的文本——直接返回给用户即可。candidate 的运行 / 打分 / 上报都在后台，**永不阻塞、永不抛错**进主路径。

可选参数：`sample_rate`（默认 `0.1`）、`tags`（归因标签）、`case_id`、`client`（自定义 `ShadowClient`）、`mock`（强制 litellm mock，默认读 `EVALGATE_MOCK_LLM`）。

完整示例见 [examples/shadow_demo/app.py](../examples/shadow_demo/app.py)。

## 配置

| 环境变量 | 作用 | 默认 |
|---|---|---|
| `EVALGATE_API_URL` | observe 推送的 EvalGate base URL | `http://localhost:8000` |
| `EVALGATE_SHADOW_WEBHOOK_URL` | 回归报警的 Slack 兼容 incoming-webhook | 未设则降级为日志 warning |

## 后端 / 运维

| 端点 | 作用 |
|---|---|
| `POST /v1/shadow/observe` | 接收一条 `(primary, candidate)` 已打分 `EvalRecord` 对，写 `shadow_observations`（返回 202）。 |
| `GET /v1/shadow/reports?candidate_prompt_hash=&window_hours=24` | 实时算窗口内 4 轴滚动报告（不落库）。 |
| `POST /v1/shadow/rollup?candidate_prompt_hash=&window_hours=24` | 算 + 落一份 `shadow_reports` 快照，回归则触发报警。 |

CLI（直连 DB，无需 HTTP）：

```bash
evalgate shadow report --candidate-hash <hash>                 # 只看
evalgate shadow rollup --candidate-hash <hash> --window-hours 24  # 落快照 + 报警；回归时退出码 1
```

生产里用 cron 周期性调 `evalgate shadow rollup`（或 `POST /v1/shadow/rollup`）即可——本项目不内置定时器。

## 报告口径

滚动报告复用与 PR CI gate **完全相同**的 `build_gate_report`：以一窗内的 primary 记录为 baseline、candidate 记录为 candidate，输出 `quality` / `cost` / `latency_p95` / `safety` 四轴（含 bootstrap CI、tag 归因、`axis_breakdown` 子轴）。`cost` 等 lower-is-better 轴显著上升即判 fail。
