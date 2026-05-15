# EvalGate · Roadmap

> 每个 phase 约 **1 人天** 的 vibe coding 工作量。每个 phase 之间是 "可独立 demo / 可独立交付" 的，
> 不会出现 "phase X 没做完，phase X+1 跑不动" 的依赖。
>
> **状态约定**：`[DONE]` 已交付（commit 已合）/ `[NEXT]` 下一步要做 / `[TODO]` 之后排队。
> 完成一个 phase 就把状态改成 `[DONE]`，并在 [`JOURNAL.md`](../JOURNAL.md) 加一条里程碑记录。
> 如果在执行中调整了路线（合并 / 拆分 / 换顺序），更新本文件并在 [`DECISIONS.md`](../DECISIONS.md) 记原因。
>
> **总体节奏**：14 个 phase，5 已完成 / 9 待办 ≈ 9 人天 vibe coding 后达到 design.md 描述的完整形态。

---

## Phase 0 · 仓库 bootstrap   [DONE]

- **目标**：可工作的 Python 仓库骨架，CI 跑通。
- **已交付**：`uv` + `ruff` + `pytest` + `pre-commit` + `docker-compose`（Postgres）+ GitHub Actions lint/test workflow + Apache-2.0 license + Dockerfile。
- **commit**：`642e8fe chore: bootstrap project (uv, ruff, pytest, docker-compose, CI)`

## Phase 1 · Walking skeleton（FastAPI + DB + OTel mapper）   [DONE]

- **目标**：trace 能写进 Postgres；最小可运行的服务端。
- **已交付**：FastAPI app（`/healthz`、`/v1/traces` ingest 端点）+ async SQLAlchemy + Alembic 初始 migration + OTel span → 内部 schema 的 mapper + 对应单测。
- **commit**：`039d9fc feat(api,db,ingest): walking skeleton — FastAPI app, async SQLAlchemy, Alembic, OTel mapper`

## Phase 2 · 多轴 CI Gate v1（fixtures 驱动）   [DONE]

- **目标**：CLI `evalgate gate` 跑通，PR 上能看到四轴报告 + tag 归因。
- **已交付**：`build_gate_report` 把 baseline/candidate JSON 算成 4 轴 metric（quality / cost / latency_p95 / safety）+ bootstrap diff CI + tag-wise attribution + GitHub Actions 自动评论 PR + 失败时阻塞 merge + demo seeder（在 billing tag 上注入 regression）。
- **commit**：`be3a749 feat(gate): end-to-end multi-axis CI gate with bootstrap CI + tag attribution`

---

## Phase 3 · OTel 端到端打通 + Trace 浏览 API   [DONE]

- **目标**：装上 OTel SDK 的真实 demo app 把 span 推到 EvalGate，DB 里能查到，REST API 能 list/detail。
- **已交付**：
  - `examples/demo_app/`：最小 Python LLM 流程（`litellm` + `mock_response`，零 API key），手写 OTel SDK + `OTLPSpanExporter` 推到 `/v1/otel/traces`。
  - `POST /v1/otel/traces` 同时接 `application/x-protobuf`（OTel SDK 默认）和 `application/json`（curl 调试友好），解析后落库 `traces` + `spans` 两表，幂等。
  - `GET /v1/traces?limit=&since=&service=` 分页列表 + `GET /v1/traces/{trace_id}` 完整 span tree 详情。
  - 持久化层 `ingest/persistence.py` 抽出 SQLite/PG 双方言的 `INSERT ... ON CONFLICT`；`traces` 汇总按 spans 表实时重算（replay 不会双计）。
  - 7 个新单测（protobuf ingest、JSON ingest、idempotency、list/detail、404）+ 现有 endpoint 测试改成真断言 DB 状态。
  - 测试用 aiosqlite in-memory engine fixture（`tests/conftest.py`），CI 不依赖 Postgres。
  - `make demo-trace`：起 DB → migrate → 启 API → 跑 demo → curl 列表 → 清理。
- **commit**：（待 commit；本地端到端 demo 已验证：3 span trace 推上去，list/detail 都返回正确 payload）
- **决策对齐**：完整落地 ADR-001（OTLP wire）+ ADR-002（PG/JSONB/Alembic）。
- **详细技术方案**：见 [docs/PHASE_3_PLAN.md](./PHASE_3_PLAN.md)。

## Phase 4 · Eval Set Manager   [DONE]

- **目标**：能把任何一条 case（来自 trace 或手工）"加入 eval set"，并按 tag 组织。
- **已交付**：
  - DB schema：`eval_sets`、`eval_cases`（`input` / `expected` / `tags`(JSONB list) / `source_trace_id`(软引用) / `source_span_id` / `task_type` enum: rag / agent / generic）+ 0003 Alembic migration + FK CASCADE。
  - REST：`POST /v1/eval-sets`、`GET /v1/eval-sets`、`GET /v1/eval-sets/{id_or_name}`、`POST /v1/eval-sets/{id}/cases`、`POST /v1/eval-sets/{id}/cases/from-trace/{trace_id}`。
  - CLI：`evalgate eval-set create / add / show`（直连 DB，`--set` 接 UUID 或 name）。
  - 抽 case 策略：从 trace 找第一个 LLM span（`evalgate.kind=llm` OR 任意 `gen_ai.*` attribute），把 prompt → `case.input`、response → `case.expected`；`task_type` 按 trace 里是否有 retriever / 多个 tool span 启发式推断。
  - 24 个新单测（pure function + REST + CLI + 404/422 路径），全部跑在 aiosqlite fixture 上不依赖 docker。
  - 退出标准：本地 promote 5 trace → 5 case，REST + CLI 都返回正确 payload。
- **commit**：（待 commit）
- **详细技术方案**：见 [docs/PHASE_4_PLAN.md](./PHASE_4_PLAN.md)。

## Phase 5 · Generic LLM-as-Judge Runner v1（LiteLLM）   [NEXT]

- **目标**：给一个 eval set + 一个 prompt（候选版本），跑 judge 出结果。
- **交付**：
  - 集成 `litellm`（默认 `gpt-4o-mini` Judge），写 `RubricJudge`：input + output → `score ∈ [0,1]` + `reason`。
  - `evalgate run --eval-set <id> --prompt path/to/prompt.yaml --out result.json`，输出格式直接喂给 Phase 2 的 `evalgate gate`。
  - 把结果落库到 `eval_run` + `eval_result` 两表。
  - 单测用 `litellm` 的 mock / record-replay（避免 CI 真调外部 API）。
- **退出标准**：能跑 baseline 和 candidate 两次，结果直接喂 `evalgate gate` 出报告。**第一次拿掉 fixtures，跑真实数据。**
- **预估**：1 天。

## Phase 6 · Judge Robustness（cross-vote + position-swap + self-consistency）   [TODO]

- **目标**：把 design.md 决策 2 的"四件套"中的去偏 + 降方差落地。
- **交付**：
  - `MultiJudge`：把 N 个 sub-judge（如 `gpt-4o-mini` + `claude-3-5-sonnet`）的结果聚合 → `(mean_score, confidence, votes)`。
  - `PositionSwapJudge` wrapper：A/B 比较时互换两次取一致才接受。
  - Self-consistency：每条 case 跑 K=3 次取多数票 + 输出 `confidence`。
  - `evalgate run --judge multi --k 3` 启用。
  - 复现实验脚本：同一 eval set 用单 judge vs multi judge 各跑 5 次，把方差对比写到 `JOURNAL.md`。
- **退出标准**：单测覆盖三种 wrapper；脚本输出方差对比表。
- **预估**：1 天。

## Phase 7 · BadCase Finder（uncertainty sampling + outlier）   [TODO]

- **目标**：自动从历史 trace 里挑值得入 eval set 的 case。
- **交付**：
  - 必须先有 Phase 5/6 的 confidence 数据（uncertainty sampling 排序的依据）。
  - `BadCaseFinder` 三层过滤：
    1. **Uncertainty**：按 Judge confidence 升序取 top-N。
    2. **启发式 outlier**：latency > p95 / cost > p95 / 用户负反馈 flag 命中。
    3. **LLM 辅助打标**：用 cheap model 给候选打 "subtle bad" 标签。
  - REST：`GET /v1/badcases?limit=20&strategy=uncertainty|outlier|llm`。
  - CLI：`evalgate badcase list --strategy uncertainty`、`evalgate badcase promote <case_id> --eval-set <id>`。
- **退出标准**：能从 100 条 mock trace 中自动挑出 10 条 BadCase 并一键 promote。
- **预估**：1 天。

## Phase 8 · RAG-aware Evaluator（RAGAS）   [TODO]

- **目标**：当 case 的 `task_type=rag` 时，走专用 evaluator。
- **交付**：
  - 集成 `ragas`：`faithfulness` + `context-precision` + `answer-relevance` 三项。
  - `EvaluatorRouter`：按 `task_type` dispatch 到 RAGAS / TrajectoryEvaluator / GenericRubricJudge。
  - `eval_case` 增加 `retrieved_contexts: text[]` 字段（migration）。
  - 一个 RAG demo eval set（5 条），跑通端到端。
- **退出标准**：单跑 `evalgate run --eval-set rag-demo` 得到三项 RAGAS metric 落库 + gate 报告里 quality 轴显示分项。
- **预估**：1 天。

## Phase 9 · Agent Trajectory Evaluator   [TODO]

- **目标**：当 case 的 `task_type=agent` 时，按"动作序列"评测。
- **交付**：
  - `TrajectoryEvaluator`：`tool_call_accuracy`（命中预期 tool 名 + 参数集合）+ `step_wise_success`（每步是否前进）。
  - `eval_case` 增加 `expected_trajectory: jsonb`（list of `{tool, args}` 步骤）。
  - 一个 Agent demo eval set（3 条多步），跑通端到端。
- **退出标准**：能正确识别 "中间步骤错但最终答案蒙对" 的 case 失败。
- **预估**：1 天。

## Phase 10 · Safety 轴落地（PII + jailbreak）   [TODO]

- **目标**：让 multi_axis.py 的 `safety` 轴是真信号而不是 demo 字段。
- **交付**：
  - `SafetyDetector`：
    - PII：用 `presidio-analyzer` 或自写 regex（email / phone / SSN）。
    - Jailbreak：先一个简单 keyword + 短小 LLM-classifier。
  - 接入 judge runner：每条 case 自动算 `safety_violation: bool`。
  - 把 `safety` 拆成 `pii_violation_rate` + `jailbreak_violation_rate` 两个 sub-axis（report 同时显示总和与拆分）。
  - 单测覆盖 PII detector 的精确率（手写 fixtures）。
- **退出标准**：往 demo eval set 注入 5 条带 PII 的 input，gate 上能看到 safety 轴 fail。
- **预估**：1 天。

## Phase 11 · Streamlit UI v1   [TODO]

- **目标**：一个能"看 trace + 看 eval set + 看 gate 报告"的 ops UI。
- **交付**：
  - 三个 page：
    1. **Traces**：分页列表 + 详情（span tree + raw attributes）。
    2. **Eval Sets**：列表 + 详情 + "从 trace 加 case" 按钮（调 Phase 4/7 API）。
    3. **Reports**：列出最近 N 次 `eval_run`，点开看 4 轴卡片 + tag 归因表。
  - `streamlit` 单容器跑 + `make ui` 启动。
- **退出标准**：在浏览器走完 trace → promote → run → 看报告 全流程。
- **预估**：1 天。

## Phase 12 · 真实 CI Gate 端到端（替换 fixtures）   [TODO]

- **目标**：CI 跑的不再是 `seed_demo.py` 的假数据，而是 Phase 5/6 真 judge 的输出。
- **交付**：
  - `eval-gate.yml` workflow 改成：(1) 拉一个固定的 reference eval set；(2) 用 PR 分支的 prompt 跑 judge；(3) 用 main 分支的 prompt 跑 judge；(4) diff → gate。
  - 一个 `examples/consumer-app/` 子仓库（或 monorepo 子目录）当作 "被评测的 LLM 应用"，作为外部接入参考。
  - prompt 用 YAML 维护，commit 在仓库里 → 满足"git-native prompt 管理"决策。
  - Judge 调用走 LiteLLM mock（CI 不烧钱），但保留可切真模型的开关。
- **退出标准**：在一个 demo PR 上把 prompt 改差一点，CI 自动 fail 且评论里指出 tag 归因。
- **预估**：1 天。

## Phase 13 · Cloud 部署（AWS ECS + RDS）   [TODO]

- **目标**：一条 `make deploy` 把服务 push 上 AWS，URL 可访问。
- **交付**：
  - Dockerfile 做成 multi-stage（builder + slim runtime），落到 < 200MB。
  - Terraform 或 AWS CDK：ECS Fargate service + ALB + RDS Postgres + Secrets Manager（OPENAI_API_KEY 等）+ ECR repo。
  - GitHub Actions deploy workflow（OIDC，无静态 AWS key）。
  - README 加 "Deployed demo: https://..." 链接。
- **退出标准**：从公网 curl healthz 通；从 demo app 推 trace 通；UI 可访问。
- **预估**：1 天（如果 AWS 账号 ready）。

## Phase 14 · Demo 打磨（数据 + 录屏 + 数字）   [TODO]

- **目标**：让简历 bullet 里的数字（±15% → ±3%、κ ~0.85、pass rate）有真实实验支撑。
- **交付**：
  - 跑一组复现实验：固定一个 eval set，单 judge vs multi-judge 各跑 10 次，统计标准差。
  - 跑 Cohen's κ：自己手工标 30-50 条 case 当人工 ground truth，对比 judge。
  - 录一段 3-5 分钟 screencast：trace 上报 → BadCase 入 eval set → 改 prompt → PR fail / pass 全流程。
  - 把数字回填到 design.md 第 4 节（简历 bullet）+ JOURNAL.md。
  - 简单 load test：`locust` 或 `wrk` 打 `/v1/otel/traces`，记 throughput / p95。
- **退出标准**：简历 bullet 不再有"虚数"。
- **预估**：1 天。

---

## 执行守则

1. **不跳 phase**。每个 phase 都有"可独立 demo"的退出标准，不要边做 phase 7 边做 phase 11。
2. **每个 phase 一个 PR / commit 块**。commit message 格式参照已有：`feat(scope): 一句话描述`。
3. **每完成一个 phase**：
   - 改本文件状态为 `[DONE]`。
   - 在 `JOURNAL.md` 顶部加一条（日期 + phase + 一段话讲做了啥 + 涉及的关键技术）。
   - 如果做的过程里改了路线 / 推翻了某个设计假设：在 `DECISIONS.md` 加一条 ADR。
4. **碰到 scope 膨胀**（一个 phase 干不完）：拆成 phase Xa / Xb，更新本文件，**不要硬塞进 1 天**。
