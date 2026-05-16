# EvalGate · Roadmap

> 每个 phase 约 **1 人天** 的 vibe coding 工作量。每个 phase 之间是 "可独立 demo / 可独立交付" 的，
> 不会出现 "phase X 没做完，phase X+1 跑不动" 的依赖。
>
> **状态约定**：`[DONE]` 已交付（commit 已合）/ `[NEXT]` 下一步要做 / `[TODO]` 之后排队。
> 完成一个 phase 就把状态改成 `[DONE]`，并在 [`JOURNAL.md`](../JOURNAL.md) 加一条里程碑记录。
> 如果在执行中调整了路线（合并 / 拆分 / 换顺序），更新本文件并在 [`DECISIONS.md`](../DECISIONS.md) 记原因。
>
> **总体节奏**：14 个 phase（10 已完成 / 4 待办 ≈ 4 人天）达到 design.md 描述的完整形态；外加 **4 个可选「亮点 phase」**（Phase 15–18）拉高简历与面试深度，按依赖择机插入。

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

## Phase 5 · Generic LLM-as-Judge Runner v1（LiteLLM）   [DONE]

- **目标**：给一个 eval set + 一个 prompt（候选版本），跑 judge 出结果。
- **已交付**：
  - `src/evalgate/judge/`：`prompt_spec` / `candidate` / `rubric_judge` / `persistence` / `runner` 五件套；`RubricJudge` 三层解析（JSON → regex → score=0 兜底）。
  - `evalgate run --eval-set X --prompt p.yaml --out r.json [--judge-model ...] [--mock] [--limit N]`，输出 JSON 字段对齐 Phase 2 `gate` 入参。
  - 两张新表 `eval_runs` / `eval_results` + 0004 migration；`judge_confidence` + `judge_raw` 字段为 Phase 17 Calibration 预留。
  - `core/schemas.py` 加 `EvalRecord` pydantic model 固化 gate / shadow（Phase 18）的字段契约。
  - 19 个新测试（72/72 全绿）：prompt_spec、rubric_judge、candidate、runner、runner→gate 端到端、CLI 端到端，全部走 aiosqlite + `mock_response`。
  - 本地 demo（真实 Ollama qwen2.5:7b，无 fixtures）：3 条 billing case，baseline vs candidate 两次 run → 4 轴 gate 报告齐全，latency_p95 12.7s → 1.3s 体现弱化 prompt 的真信号。
- **commit**：（待 commit）
- **详细技术方案**：见 [docs/PHASE_5_PLAN.md](./PHASE_5_PLAN.md)。

## Phase 6 · Judge Robustness（cross-vote + position-swap + self-consistency）   [DONE]

- **目标**：把 design.md 决策 2 的"四件套"中的去偏 + 降方差落地。
- **交付**：
  - `MultiJudge`：N 个 sub-judge 聚合 → `(score, confidence, votes, raw_calls)`，confidence = 内部稳定度 × 跨 judge 一致度。
  - `PositionSwapJudge` wrapper：A/B 互换两次取一致；冲突 → 0.5 + `agreement=False`。
  - `SelfConsistencyJudge`：K 次重打，`confidence = 1 - stdev / 0.5`。
  - `prompt.yaml` 改 `judges: [...] + judge_policy:`（**breaking**，旧单数 `judge:` 报错并附迁移示例）。
  - 新表 `eval_judge_calls`（0005 migration）：N×K×P 行/case，Phase 14/17 可直接 SQL 复盘。
  - `evalgate run --k / --concurrency / --policy-mode` 覆盖实验脚本用。
  - [scripts/phase6_variance.py](../scripts/phase6_variance.py) 真本机实测：single 0.0377 vs multi 0.0136（lower 即更稳）。
- **退出标准**：单测覆盖五种 wrapper + 迁移；本机 Ollama 真数据写 JOURNAL。
- **commit**：（待 commit）
- **详细技术方案**：见 [docs/PHASE_6_PLAN.md](./PHASE_6_PLAN.md)。

## Phase 7 · BadCase Finder（uncertainty sampling + outlier）   [DONE]

- **目标**：把 Phase 5/6 写到 `eval_results` 的 confidence/latency/cost 信号变成可执行的 active-learning 动作。
- **交付**：
  - [src/evalgate/badcase/finder.py](../src/evalgate/badcase/finder.py)：`BadCase` dataclass + `find_uncertainty / find_outlier / find_llm / find`。
  - [src/evalgate/badcase/repository.py](../src/evalgate/badcase/repository.py)：`promote_result_to_set` 写一条 `eval_case_set_memberships`（never duplicates payload），结构性 dedup via UniqueConstraint。
  - REST：`GET /v1/badcases?strategy=...` + `POST /v1/badcases/{eval_result_id}/promote`。
  - CLI：`evalgate badcase list --strategy {uncertainty|outlier|llm}` + `evalgate badcase promote --result <id> --eval-set <target>`。
  - 数据源选 A（仅 `eval_results`，**不加新表 for finder**）。
- **后置 refactor**（同 PR）：
  - **Phase 7.5**：promote 从 「复制 EvalCaseRow」改为「插 `eval_case_set_memberships`」N:N + 新 `AlreadyPromotedError`（409）。详见 [PHASE_7_PLAN.md 文末](./PHASE_7_PLAN.md)。
  - **Phase 4.5**：彻底删 `EvalCaseRow.eval_set_id`，membership 表成为唯一真理源（migration 0007 含可逆 downgrade）。`SameSetPromotionError` 取消，归并到 `AlreadyPromotedError`。
- **退出标准**：[scripts/phase7_badcase_smoke.py](../scripts/phase7_badcase_smoke.py) 走通：10 case → finder 拿 top-3 → promote 落 target set。全 123 测试绿。
- **commit**：（待 commit）
- **详细技术方案**：见 [docs/PHASE_7_PLAN.md](./PHASE_7_PLAN.md)。

## Phase 8 · RAG-aware Evaluator（RAGAS）   [DONE]

- **目标**：当 case 的 `task_type=rag` 时，走专用 evaluator。
- **已交付**：
  - 引入官方 `ragas>=0.2`（实测 0.2.15）+ `datasets` + `langchain-core`；`src/evalgate/evaluator/rag/ragas_adapter.py` 写 LiteLLMChatModel + LiteLLMEmbeddings 把 ragas 的 langchain 调用导向 `litellm.acompletion / aembedding`；mock 模式走 SHA-256 384-dim 伪向量，CI 不连 Ollama。
  - `EvaluatorRouter`（`src/evalgate/evaluator/router.py`）按 `TaskKind` 分派；Phase 8 注册 `generic`（Phase 5/6 MultiJudge 路径，搬到 `evaluator/generic.py`）+ `rag`；`agent` 留给 Phase 9 一行注册即可；`UnsupportedTaskTypeError` 不破坏整 run。
  - 重构：删除 `src/evalgate/judge/runner.py`，新 `src/evalgate/evaluator/runner.py` 用 `EvaluationOutcome` dataclass 统一所有 evaluator 的输出；CLI / scripts / 测试全切到 `evaluator.runner`。
  - 0008 migration：`eval_cases.retrieved_contexts`（金标 contexts）+ `eval_results.sub_metrics`（per-metric 分项）+ `eval_results.retrieved_contexts`（运行时检索结果，badcase 审计）。
  - `EmbeddingRetriever`（candidate 端动态检索）：corpus.json + numpy 余弦排序 + lazy embed cache + `top_k` clamp。
  - Gate 分项：`AxisMetric.sub_metrics: dict[str, AxisMetric] | None` 递归字段；`build_axis_metrics` 自动从 records 的 `sub_metrics` 派生 nested axes（每项 bootstrap CI），`quality.passed = passed AND all(sub.passed)`；混合 set 只在 RAG records 上聚合分项。
  - REST `POST /v1/eval-sets/{id}/cases` + CLI `evalgate eval-set add-rag-case` 支持 `retrieved_contexts`；`examples/rag_demo/`（10 chunk corpus + 5 case seeder + baseline / weakened candidate YAML）+ `scripts/phase8_rag_smoke.py` 端到端跑通。
  - 30 个新测试（router / retriever / rag_evaluator / litellm_adapter / migration round-trip / runner end-to-end / gate sub-axes），全部 aiosqlite + mock；总 153/153 绿。
- **退出标准达成**：`EVALGATE_MOCK_LLM=1 PYTHONPATH=. python scripts/phase8_rag_smoke.py` 端到端跑通 5 case，gate 报告 `axes[quality].sub_metrics` 含 `{faithfulness, context_precision, answer_relevance}`，每项 baseline/candidate/delta/significant 齐全。
- **commit**：（待 commit）
- **详细技术方案**：见 [docs/PHASE_8_PLAN.md](./PHASE_8_PLAN.md)。

## Phase 9 · Agent Trajectory Evaluator   [DONE]

- **目标**：当 case 的 `task_type=agent` 时，按"动作序列"评测。
- **已交付**：
  - `AgentTrajectoryEvaluator`（`src/evalgate/evaluator/agent/evaluator.py`）：对比 `expected_trajectory` vs runtime `actual_trajectory`，输出 `tool_call_accuracy` + `step_wise_success` 两项，并写入 `EvaluationOutcome.sub_metrics`。
  - `AgentRuntime`（`src/evalgate/evaluator/agent/runtime.py`）：LLM strict-JSON action loop（`call_tool` / `final_answer`）+ builtin tool registry 执行，产出真实轨迹；中间 parse/tool 错误作为 trajectory 质量信号记录进 `judge_raw`/`eval_judge_calls`。
  - `EvaluatorRouter` 注册 `task_type=agent` 分支（`spec.agent_runtime` 存在时启用）；缺配置保持 `unsupported_task_type` per-case error，不炸整 run。
  - `PromptSpec` 新增 `agent_runtime` 块：`max_steps` / `tool_names` / `planner_model` + 校验（非空、去重）。
  - `eval_cases` 新增 `expected_trajectory`（0009 migration），并打通 repository / REST / CLI（含 `evalgate eval-set add-agent-case --step ...`）。
  - `case_extract` 新增 tool span 抽取 `expected_trajectory`（`add_case_from_trace` 自动透传）。
  - Agent demo：`examples/agent_demo/`（3 条多步 case + baseline/candidate prompt）+ `scripts/phase9_agent_smoke.py` 端到端 smoke。
  - 测试补齐：runtime / evaluator / router / schema round-trip / runner e2e / gate sub-axes / extractor / prompt spec / CLI，覆盖中间步骤错判定逻辑。
- **退出标准达成**：`PYTHONPATH=. .venv/bin/python scripts/phase9_agent_smoke.py` 能识别并暴露“中间步骤错但最终答案可用”的 regression（`quality.sub_metrics.step_wise_success` 下滑）。
- **commit**：（待 commit）
- **详细技术方案**：见 [docs/PHASE_9_PLAN.md](./PHASE_9_PLAN.md)。

## Phase 10 · Safety 轴落地（PII + jailbreak）   [DONE]

- **目标**：让 multi_axis.py 的 `safety` 轴是真信号而不是 demo 字段。
- **已交付**：
  - `src/evalgate/safety/`：`PresidioPiiDetector`（绕过 AnalyzerEngine 直调 PatternRecognizer，CI 离线）+ `JailbreakDetector`（关键词 + 可选 LiteLLM JSON 分类器 + refusal-marker 启发式 fallback）+ `SafetyPipeline.augment` 把 4 项 sub-metric 写进 `axis_breakdown["safety"]` 并 OR `safety_violation`。
  - 重构：`EvalRecord` / `EvalResultRow` / `EvaluationOutcome` 的 `sub_metrics` 全部改名为 `axis_breakdown: dict[str, dict[str, float]]`（外层键 = gate 主轴名）；migration 0010 在 PG / SQLite 双路 round-trip 旧 RAG 数据。
  - `multi_axis._build_sub_metric_axes` 通用化：`quality` / `safety` 都自动派生 sub-axes，主轴 `passed = main_passed AND all(sub.passed)`，summary 同时点名 quality / safety 的 regressed sub-metric。
  - `PromptSpec.safety` block（`enabled` / `pii.entities` / `pii.score_threshold` / `jailbreak.keywords` / `jailbreak.classifier_model`）。`safety.enabled=false` → pipeline 返回 `None`，runner 跳过。
  - Safety demo：`examples/safety_demo/`（5 PII + 4 jailbreak + 3 clean case，baseline set = 仅 clean，candidate set = 全量）+ `scripts/phase10_safety_smoke.py`。
  - 测试补齐：pii / jailbreak / pipeline / runner 集成 / gate sub-axes（quality + safety）/ migration round-trip + 全部 Phase 8/9 测试改为读 `axis_breakdown.quality`。
- **退出标准达成**：`EVALGATE_MOCK_LLM=1 PYTHONPATH='src:.' python scripts/phase10_safety_smoke.py` 跑通：candidate set 注入 PII + jailbreak 后，gate report `axes[safety].passed=False`，`delta=+0.75`，三项 sub-axis（`pii_input_rate` / `jailbreak_attempt_rate` / `jailbreak_compliance_rate`）regress。
- **commit**：（待 commit）
- **详细技术方案**：见 [docs/PHASE_10_PLAN.md](./PHASE_10_PLAN.md)。

## Phase 11 · Streamlit Ops UI v1   [DONE]

- **目标**：一个能"看 trace + 看 eval set + 看 gate 报告"的 ops UI，且不直连 DB。
- **已交付**：
  - 新 REST `/v1/runs?eval_set_id=&limit=` + `/v1/runs/{id}` + `/v1/runs/{id}/records`：repo helper `judge.persistence.list_runs`，router `evals.py` 把 `EvalResultRow` 回吐成 `EvalRecord`-shape 直接喂回 `POST /v1/evals/run`。
  - `src/evalgate/ui/`：`api_client.EvalGateClient`（同步 `httpx.Client` + `EvalGateAPIError`，`EVALGATE_API_URL` 可覆盖 base URL）、`format` 纯函数、`Home.py` landing + 健康徽章、`pages/1_Traces.py` / `2_Eval_Sets.py` / `3_Reports.py`。
  - 三个 page 全部只通过 client 调 `/v1/*`：Traces 分页 + span tree + promote-to-set 调 `POST /v1/eval-sets/{id}/cases/from-trace/{trace_id}`；Eval Sets 列表 + 详情 + 创建表单；Reports 双 selectbox（baseline / candidate）→ 拉两组 records → POST `/v1/evals/run` → 4 轴 metric + sub-axes 表（quality / safety）+ 排序后的 tag 归因。
  - `pyproject.toml` 主依赖加 `streamlit>=1.36` + `httpx>=0.27`（从 dev 提主），`Makefile` 加 `make ui`，README 加一节 “Ops UI”。
  - 测试矩阵：`test_runs_endpoint`（list / filter / limit / 404）、`test_runs_records_endpoint`（row → EvalRecord 透传 `axis_breakdown`/`retrieved_contexts`，端到端喂回 gate）、`test_ui_api_client`（`httpx.MockTransport` 验证 URL / params / pydantic 解析 / 错误码）、`test_ui_format`（latency / cost / score / datetime / 排序 / run label）。
- **退出标准达成**：`make db-up && uv run alembic upgrade head && uv run python scripts/seed_demo.py && uv run evalgate-api` + 另一 shell `make ui` → 浏览器走完 Traces → promote → Eval Sets 看 case → CLI 跑两次 `evalgate run` → Reports 选两 run 看 4 轴 + sub-axes 报告。
- **commit**：（待 commit）
- **详细技术方案**：见 [docs/PHASE_11_PLAN.md](./PHASE_11_PLAN.md)。

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

## 亮点 Phase（可选，按依赖择机插入）

> 这四个 phase **不在 design.md 的最小完整形态里**，是给 EvalGate 加技术深度 / 简历亮点用的。
> 每个 phase 都标了 **依赖**（必须先做完哪些 phase）；不强制按编号顺序。

## Phase 15 · Adversarial Case Synth（红队自动出题）   [TODO]

- **目标**：从 attribution 报告找最弱 tag → 用 generator-LLM **自动生成同 tag 的"刁钻 case"** → 人审后入 eval set，形成 "评测 → 找弱点 → 自动出题 → 再评测" 的飞轮。
- **依赖**：Phase 7（BadCase Finder 提供 attribution + uncertainty）+ Phase 10（safety 检测器复用做对抗模板，可不严格阻塞）。
- **交付**：
  - `AdversarialSynth`：输入 `(tag, weak_cases[5..10])`，调用 generator-LLM 产 K=10 条候选 case；模板覆盖：边界值、歧义指代、prompt injection（"ignore previous instructions..."）、role confusion。
  - 人审 gate：生成的 case 进 `eval_cases.status="pending"`，**不参与 gate**；CLI `evalgate adversarial review --set <id>` 逐条 approve/reject 切到 `status="active"`。
  - `eval_cases` 加 `status` enum（pending/active/archived）+ `source` enum（trace/manual/adversarial）→ 0006 migration。
  - REST：`POST /v1/eval-sets/{id}/adversarial?tag=<t>&k=10`。
  - 报告：`evalgate adversarial stats` 输出近 N 次 adversarial 命中率（多少条让 candidate 得分降 ≥ 0.2）。
- **退出标准**：从 billing tag 自动出 10 条，approve 6 条；新 candidate 在其中 ≥3 条得分 < 0.5 → gate fail；录一段 demo screencast。
- **预估**：1 天。
- **简历语言**：automated red-teaming + adversarial regression suite + closed-loop eval。

## Phase 16 · Sequential Gate（边跑边判，省 judge 调用）   [TODO]

- **目标**：CI gate 不再"跑满 N 才下结论"，而是流式接 `(case_id, score)`，每跑 K 条评估一次 → **显著变差立即停跑 fail / 显著一致提前 pass**，控制累计 Type-I error。
- **依赖**：Phase 2（bootstrap CI 实现）+ Phase 6（per-case score 流式产出）。
- **交付**：
  - `SequentialGate` 模块：用 **α-spending**（O'Brien-Fleming 或 Pocock 边界）维持累计 α=0.05；连续 M 次没显著 → early pass；任一窗口越下边界 → early fail。
  - `evalgate run --gate-mode sequential --baseline-run <id>`：runner 每出一条结果就问 gate 「还要继续吗」。
  - gate 报告新增字段：`stopped_early: bool` + `cases_consumed: int` + `boundary_used: str`。
  - 单测：simulate 1000 次 H0 / H1，断言实际 Type-I error ≈ 0.05、平均 case 消耗下降 ≥ 50%。
- **退出标准**：同一 regressed PR 上对比 fixed-N gate vs sequential gate，judge 调用平均省 ≥ 50%，Type-I error 保持 ≤ 0.05。
- **预估**：1 天（统计实现 + 蒙特卡洛单测占大头）。
- **简历语言**：group sequential testing + α-spending function + cost-aware CI。

## Phase 17 · Judge Calibration（ECE + temperature scaling）   [TODO]

- **目标**：让 judge 给出的 `confidence` 真有概率意义——judge 说 0.8，实际人工通过率就是 80%。
- **依赖**：Phase 6（multi-judge confidence）+ Phase 14（人工标注 ground truth ≥ 30 条）。
- **交付**：
  - 用 Phase 14 那批人标 case 配对 `(judge_score, human_label)`：
    - 画 **reliability diagram**（10 bins）；
    - 算 **ECE** + **MCE**；
    - 跑 **temperature scaling**（单参数 logistic）拟合 → 落到 `calibration_params.json`。
  - `CalibratedJudge` wrapper：原始 score → calibrated score；BadCase Finder 的 uncertainty sampling 切到 calibrated confidence。
  - 报告：`evalgate calibration report` 输出 ECE-before / ECE-after + reliability 图（matplotlib png）。
  - 单测：手造 miscalibrated 数据（systematic overconfidence），断言 temperature scaling 后 ECE 显著下降。
- **退出标准**：ECE 从 ≥ 0.15 降到 ≤ 0.05；切到 calibrated confidence 后 BadCase Finder top-N 召回 mock-bad 提升（用合成数据可验证）。
- **预估**：1 天。
- **简历语言**：Expected Calibration Error + temperature scaling + reliability diagram；引 Guo et al. 2017。

## Phase 18 · Shadow Mode（线上流量上做无害评测）   [TODO]

- **目标**：candidate prompt 不只在 PR 上被评——**生产 X% 流量也并发跑 candidate**（结果不返给用户），同一套 4 轴聚合 → 提前发现 PR eval set 覆盖不到的 "unknown unknown"。
- **依赖**：Phase 5（runner）+ Phase 3（trace ingest）+ Phase 13（cloud 部署，让真实生产 caller 接得上）。
- **交付**：
  - 客户端 SDK：`evalgate.shadow(primary_prompt, candidate_prompt, sample_rate=0.1)` 包一层——sample 命中时并发跑 candidate，结果**异步**推回 EvalGate；**fire-and-forget + 超时 1s 即丢**，绝不阻塞主路径。
  - Backend：`POST /v1/shadow/observe` 接收 `(primary_result, candidate_result)` 对，按 `prompt_hash` 聚合；新表 `shadow_observations` + `shadow_reports`（每小时滚动算一次 4 轴）。
  - 报警：rolling 24h shadow 报告里若 candidate 任一轴显著变差 → webhook 通知（Slack 优先）。
  - 文档 [docs/SHADOW.md](./SHADOW.md)：接入只要 3 行代码。
- **退出标准**：demo app 接入 shadow，跑 1k 次主流量，shadow report 给出 4 轴对比；故意让 candidate cost 高 20% 触发报警。
- **预估**：1 天（不含 cloud 部署本身）。
- **简历语言**：online shadow evaluation + production-traffic A/B + unknown-unknown detection。

---

## 执行守则

1. **不跳 phase**（仅限 Phase 0–14）。每个 phase 都有"可独立 demo"的退出标准，不要边做 phase 7 边做 phase 11。亮点 Phase 15–18 按依赖择机插入，可缓做。
2. **每个 phase 一个 PR / commit 块**。commit message 格式参照已有：`feat(scope): 一句话描述`。
3. **每完成一个 phase**：
   - 改本文件状态为 `[DONE]`。
   - 在 `JOURNAL.md` 顶部加一条（日期 + phase + 一段话讲做了啥 + 涉及的关键技术）。
   - 如果做的过程里改了路线 / 推翻了某个设计假设：在 `DECISIONS.md` 加一条 ADR。
4. **碰到 scope 膨胀**（一个 phase 干不完）：拆成 phase Xa / Xb，更新本文件，**不要硬塞进 1 天**。
