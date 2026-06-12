# DECISIONS · 核心技术决策日志

> 本文件记录 EvalGate **真正影响架构 / 路线 / 长期可维护性**的技术决策。
> 不记 "用了 ruff 而不是 black" 这种纯偏好；记的是 "未来某个工程师接手会问『为什么不那样做？』" 的决策。
>
> 格式参考 ADR（Architecture Decision Record），但不严格 — 关键四要素：**Context（背景）/ Decision（决策）/ Rationale（为什么）/ Consequences（代价）**。
>
> 决策一旦写下就**不删除、不重写**；如果反悔了，**新加一条** `Status: superseded by ADR-N` 的反转决策，保留思考轨迹。
>
> 编号单调递增。新决策追加在文末。

## 决策索引（ADR index）

| # | 决策 | 状态 | 一句话 |
|---|---|---|---|
| **ADR-001** | OTel 作为 trace 协议 | accepted | 用开放标准换"应用方零迁移 + 无 vendor lock-in" |
| **ADR-002** | Postgres + JSONB | accepted | schema-less 字段用 JSONB，兼顾灵活与 SQL 能力 |
| **ADR-003** | 砍掉 Prompt 管理 UI | accepted | prompt 当配置文件交给 git，聚焦"评测" |
| **ADR-004** | 四轴 + 显著性 + 归因 gate | accepted（Phase 2） | 覆盖漏判 / 误 block / 不可解释三个坑 |
| **ADR-005** | 任务分层 + 多 judge 去偏 | accepted（Phase 5/6/8/9） | 降方差 + 去 bias，覆盖 RAG / Agent / 通用 |
| **ADR-006** | UI 用 Streamlit | accepted | 运维向 dashboard，省下时间投 backend |
| **ADR-007** | 用 `uv` 管包 | accepted | 速度快、单二进制、PEP 621 兼容 |
| **ADR-008** | LiteLLM 统一 LLM 调用 | accepted（Phase 5） | 一个接口调 100+ provider，支撑 cross-vote |
| **ADR-009** | CI gate 跑 mock judge + ephemeral SQLite | accepted（Phase 12） | CI 离线确定性零成本，真模型走 `make ci-gate-real` |
| **ADR-010** | Shadow Mode：SDK 侧打分 + on-demand rollup | accepted（Phase 13） | 后端保持薄层，复用 `EvalRecord` + `build_gate_report`，不背 scheduler 依赖 |

> 阅读顺序提示：每条决策都按 **Context（背景）→ Decision（决策）→ Rationale（为什么）→ Consequences（代价）** 四段展开。

---

## ADR-001 · 用 OpenTelemetry 作为 trace 协议，不做自家 SDK

**Date**: 2026-05-14 · **Status**: accepted

**Context**：业内同类产品（LangSmith、Langfuse 早期）都做自家 SDK 来上报 trace，能塞更多 metadata，体验顺滑；OpenTelemetry / OTLP 是更开放的业界标准，应用方一行 instrumentor 就接入。

**Decision**：所有 trace ingest 走 OTLP（HTTP / gRPC），不提供也不计划提供 EvalGate 自家 SDK。

**Rationale**：
1. 应用方接入成本是 B 端工具的首要决定因素。OTel 装一个 `opentelemetry-instrumentation-openai` 就能上报，自家 SDK 要改业务代码。
2. **避免 vendor lock-in 是企业客户最在意的卖点**。客户未来想换 backend（Datadog / Honeycomb / Phoenix）零迁移成本。
3. 开源生态（`openinference` / `openllmetry`）已经把 LLM-specific 的 semantic convention 推得差不多了，搭顺风车而不是另起炉灶。

**Consequences**：
- 需要写 OTel attribute → 内部 `traces` + `spans` 数据模型的 mapper（已落在 `src/evalgate/ingest/otel_mapper.py`）。
- 失去对 SDK 体验的精细控制，遇到边角字段缺失要等上游 / 自己提 PR。
- ingest 路径必须能消化"未来不确定 attribute"，所以选 JSONB 列存（见 ADR-002）。

---

## ADR-002 · Postgres + JSONB 而非 NoSQL（Mongo / DynamoDB）

**Date**: 2026-05-14 · **Status**: accepted

**Context**：OTel span 的 `attributes` 是 schema-less key-value，传统 RDBMS 表达起来很别扭；NoSQL 天然适合。但 EvalGate 的核心查询场景是 "按 tag 聚合"、"按时间窗口算 p95"、"join eval_run × eval_case" — 这些都是 SQL 强项。

**Decision**：
- 主存储用 **Postgres**。
- 不固定 schema 的字段（OTel attributes、judge raw output、tool args）用 **JSONB** 列。
- schema 演进用 **Alembic** 显式 migration。

**Rationale**：
1. JSONB 在 PG 上是一等公民，可以建 GIN 索引，`->`、`->>`、`@>` 操作都很顺。
2. 团队（=我自己）SQL 比 Mongo 熟太多，bootstrap 速度优先。
3. 单实例 PG 撑到几千万行 trace 不是问题；真到那个量级再切 ClickHouse 也来得及（届时 trace 表是冷写热读，很容易迁）。
4. RDS 上托管 Postgres 是 AWS 一等支持，Phase 13 的部署成本可控。

**Consequences**：
- 高吞吐 OTLP ingest 要靠 async + batch insert 顶（FastAPI + asyncpg + `COPY` 或多行 insert）。
- 后期如果 trace 量级到 10^9，需要切到列存（ClickHouse）或冷热分层（PG 热 + S3 + Athena 冷）。届时新加 ADR。

---

## ADR-003 · 砍掉 Prompt 管理 UI，prompt 当配置文件（git-native）

**Date**: 2026-05-14 · **Status**: accepted

**Context**：LangSmith / PromptLayer 都做了重型 prompt hub（version diff、A/B、UI 编辑）。看起来很全，是不是要跟？

**Decision**：**完全不做** prompt 管理 UI。Prompt 以 YAML / Python 模块形式 commit 在应用方仓库，由 git 自然管版本。EvalGate 只负责"评测一个给定的 prompt"。

**Rationale**：
1. 这是红海，5+ OSS 工具都做了，再做一个零差异化。
2. UI 工作量翻倍，但 differentiation 为零 — 与其做这个不如把 evaluator 深度做透。
3. **强化"Eval-First"定位**：我们不替代 prompt 工具链，我们是 prompt 改动的"质检岗"。
4. Prompt as code → 跟 PR / code review / git blame 自然集成，是更工程化的形态。

**Consequences**：
- 应用方需要自己定 prompt 文件格式（YAML / Jinja / Python module）。我们提供 example schema 但不强约束。
- 失去 "non-engineer 改 prompt" 这个用户群（PM / 标注员），但他们本来也不是我们的目标用户。

---

## ADR-004 · CI Gate 是"四轴 + bootstrap CI 显著性 + tag 归因"，不是单 pass rate

**Date**: 2026-05-14 · **Status**: accepted（Phase 2 已实现 v1）

**Context**：市面 OSS eval 工具默认形态是"pass rate 跌破阈值 → fail"。这种 gate 在生产里有三个已知坑：
- 漏判：pass rate 不变但 cost 翻倍 / latency p95 涨 2 倍 / safety violation 多了。
- 误 block：LLM eval 是 stochastic 的，92% → 89% 可能只是噪声；误 block 一次，下次所有人 `--force` 跳过 gate，**整个系统就废了**。
- 不解决问题："pass rate 跌了 3%" 是 alarm 不是 root cause，开发者还得自己翻 trace。

**Decision**：CI gate 必备三件套 —
1. **多轴**：quality / cost / latency_p95 / safety 四轴并联，任一轴 regress 即 fail。
2. **统计显著性**：mean 类轴用 **bootstrap CI（1000 次重采样，95%）**，CI 不跨 0 才算真 regression。p95 类轴 v1 先用阈值（重采样的 p95 解释比较微妙，留待 Phase 17 复盘）。
3. **tag 归因**：每条 case 打 tag，failed 时报告"哪个 tag 簇集体跌了"，而不是只给整体数字。

**Rationale**：
1. 漏判靠多轴覆盖，误 block 靠显著性判断，不可解释靠 tag 归因 — 三者缺一就是 demo 而不是产品。
2. bootstrap 比 paired t-test 对分布形状不敏感，eval 分数经常是非正态的（双峰或截断），bootstrap 更稳。
3. 让 gate 是"开发者愿意保留"而不是"绕过去"的形态，这是整个产品成立的前提。

**Consequences**：
- Bootstrap 计算量 = O(N × resamples)，对 eval set 几百条 × 1000 重采样在毫秒级，相比 judge 调用本身可忽略。
- Tag 维护成本下放给应用方（在 prompt / case 里手工或半自动打 tag）。
- p95 显著性留了技术债，Phase 17 要复盘。

---

## ADR-005 · 任务分层 evaluator + 多 judge cross-vote + position-swap + self-consistency

**Date**: 2026-05-14 · **Status**: accepted（Phase 5/6/8/9 待落地）

**Context**：纯 LLM-as-Judge（单模型 + 单次调用 + 通用 rubric）在 2026 已经是 baseline，至少有三类已知缺陷：
- 单次方差 ±15%（同 input 跑 3 次给不同分）。
- 任务异质：RAG 看引用忠实度、Agent 看动作序列、通用看回答质量，同一 rubric 必然失真。
- 已知 bias（Zheng 2023 MT-Bench）：position bias / verbosity bias / self-preference bias。

**Decision**：四件套 —
1. **任务分层 evaluator**：RAG → RAGAS；Agent → trajectory eval（tool-call accuracy + step-wise success）；通用 → rubric LLM-as-Judge。`EvaluatorRouter` 按 `eval_case.task_type` 分发。
2. **多 judge cross-vote**：跨家族（GPT-4 + Claude）防 self-preference bias。
3. **去偏 wrapper**：position-swap（A/B 互换两次取一致）+ verbosity normalization（按长度归一）。
4. **self-consistency**：每条 case judge 跑 K=3 次取多数票 + 输出 confidence。

**Rationale**：单 judge 的方差和 bias 是论文/工业界共识，不修就没法做"显著性"判断（gate 会被噪声主导）。任务分层是 evaluator 质量的根本约束 — 不分层，RAG 和 Agent 共用 rubric 必然两边都不准。

**Consequences**：
- **评测成本 ×6-10**（多模型 × 多次调用）。这是有意识接受的代价，因为 CI gate 的"可信度"是产品的根本。生产部署时可加 caching / sampling 把成本压回 ×2-3。
- 复杂度大幅上升 — 多了 `MultiJudge` / `PositionSwapJudge` / `EvaluatorRouter` 等抽象层。Phase 6 必须有专门复现实验脚本验证方差真的从 ±15% 降到 ±3%（不然这个决策站不住脚）。

---

## ADR-006 · UI 用 Streamlit 不用 React/Next.js

**Date**: 2026-05-14 · **Status**: accepted

**Context**：作为一个 ops / 数据展示型平台，UI 是必须的；但 React 全家桶 ramp-up 成本高，且本项目战略重心在 backend / eval 算法。

**Decision**：UI 用 **Streamlit** 单容器，前后端不分离。

**Rationale**：
1. Streamlit 写运维向 dashboard 比 React 快 5-10 倍。
2. 用户群体（ML 工程师 / DevOps）不挑剔交互，能看清数据就行。
3. 把节省的前端时间投到 evaluator 算法和 cloud 部署上 —  这两块才是简历能讲故事的地方。
4. 后期如果有 SaaS / 多租户需求，再切 Next.js + 独立 backend，到时数据 API 已经是 REST，前端是可换件。

**Consequences**：
- UI 不能做高度自定义交互（拖拽、复杂表单），但本项目场景用不上。
- Streamlit session state 模型有点反直觉，需要约定 page 间状态用 query params 传递。

---

## ADR-007 · 用 `uv` 做 Python 包管理 / venv

**Date**: 2026-05-14 · **Status**: accepted

**Context**：Python 包管理 2024-2026 处于换代期 — pip / poetry / pdm / rye / uv 多选项。

**Decision**：用 **uv**（`uv sync`、`uv run`、`uv lock`）。

**Rationale**：
1. 速度比 poetry 快 10-100×，CI 时间显著缩短。
2. 单二进制，零 Python bootstrap 依赖（不需要先有一个 Python 来装包管理器）。
3. 与 PEP 621 `pyproject.toml` 标准格式兼容，未来切换到其他工具成本低。

**Consequences**：
- 团队成员需要装 uv（CI 已用 `astral-sh/setup-uv@v3` 解决）。
- uv 还在快速演进，偶有破坏性更新，需关注 release notes。

---

## ADR-008 · LiteLLM 统一 LLM 调用层

**Date**: 2026-05-14 · **Status**: accepted（Phase 5 待引入）

**Context**：Judge 需要跨家族调多个模型（GPT-4 + Claude + 可能 Gemini）。直接各家 SDK 写一遍，代码膨胀且难做 cross-vote 抽象。

**Decision**：所有外部 LLM 调用走 **LiteLLM**（`completion()` 统一接口）。

**Rationale**：
1. 一个接口 100+ provider，加 / 切模型零成本。
2. 自带 retry / fallback / cost tracking，省去自己写。
3. CI 测试时可以用 LiteLLM 的 mock / record-replay，避免真烧 API quota。
4. 直接对应 ADR-005 的 multi-judge cross-vote 需求。

**Consequences**：
- 多一层抽象，遇到极少数 provider-specific feature（如 Anthropic 的 prompt caching）需要绕一下。
- 依赖 LiteLLM 维护节奏 — 它非常活跃，目前不是问题。

---

## ADR-009 · CI gate 用 mock judge + ephemeral SQLite，真模型走显式手动入口

**Date**: 2026-06-11 · **Status**: accepted（Phase 12）

**Context**：Phase 12 把 CI 卡口从静态 fixtures 换成真 judge 流水线（seed reference set → run baseline prompt → run candidate prompt → diff gate）。但 GitHub Actions 上跑真 LLM 有三个坑：(1) 烧 token / 需要把 API key 放进 CI secret；(2) judge 是随机性的，PR 之间的卡口结论会抖、复现难；(3) `evalgate run` 要写库，CI 还得起一个 Postgres service。而本仓库自身的 PR 大多跟 prompt 质量无关（改文档、改 ingest 代码……），用真模型评测它们既贵又会产生无意义的"回归"噪声。

**Decision**：
- CI 的 `eval-gate` workflow 跑 `EVALGATE_MOCK_LLM=1` —— judge / candidate / ragas 全部走 LiteLLM mock，离线、确定性、零成本。
- CI 这步的语义是**端到端连通性 smoke**：断言每个 task_type 都产出非 error record、gate 报告含四轴 + RAG/agent quality 子项 + safety 子项；mock 下 baseline/candidate 同集各轴一致 → gate 必过。
- 真模型评测走显式手动入口：`make ci-gate-real`（本机 Ollama）或 `workflow_dispatch` 去掉 mock。
- orchestrator（`scripts/phase12_ci_gate.py`）在 CI 用 **ephemeral SQLite**（`Base.metadata.create_all`，不跑 alembic），不依赖 Postgres service。

**Rationale**：
1. **CI 应该测"流水线没断"，不是"这个 PR 的 prompt 好不好"** —— 后者是 consumer 仓库接入 EvalGate 后、在它们自己的 prompt PR 上才有意义的判断。把两件事分开，CI 才稳。
2. mock 确定性 = 卡口不会因 judge 抖动随机红 / 绿，团队不会因为"误 block"去关掉卡口（正是 ADR-004 想避免的失败模式）。
3. 零 token、无需 CI secret，安全面更小。
4. ephemeral SQLite 让 CI job 无状态、无外部依赖，和各 phase 的 smoke 脚本同构（同一套 dialect-agnostic repository 代码路径，见 ADR-002）。

**Consequences**：
- CI 不会自动抓真实质量回归 —— 那是 consumer 仓库接入后在它们的 prompt PR 上、或本仓库手动 `make ci-gate-real` 时才发生。退出标准里"改差 prompt → CI fail + 归因"是用真模型在本地复现的（实测 ~140s）。
- mock judge 恒返 0.5，所以 CI 这步无法验证"显著性判定"本身的正确性 —— 那由 `report/significance.py` 的单测和 Phase 17 的复现实验覆盖。
- 想在 CI 上真跑模型，需要自备 self-hosted runner + 模型，经 `workflow_dispatch` 去 mock。

---

## ADR-010 · Shadow Mode 在 SDK 侧打分，rollup 走 on-demand 而非内置 scheduler

**Date**: 2026-06-11 · **Status**: accepted（Phase 13）

**Context**：Shadow Mode 要在生产流量上无害评测 candidate。两个绕不开的设计岔路：(1) 生产没有人工 ground truth，primary / candidate 的分数从哪来、谁来算？(2) "每小时滚动算一次 4 轴 + 报警" 这种周期任务，要不要在服务里塞一个定时器 / 后台 worker？

**Decision**：
- **打分放客户端 SDK**：`evalgate.shadow(...)` 命中采样后，后台并发跑 candidate，并复用 `build_judge_stack(primary)` 给 primary / candidate **两边用同一 rubric** 打 reference-free 分，打包成两条 `EvalRecord` 推到 `POST /v1/shadow/observe`。后端只做"写 observation + 按 `candidate_prompt_hash` 聚合"，不跑 judge。
- **滚动报告 on-demand + 显式 rollup**：`GET /v1/shadow/reports` 实时算窗口内 4 轴（不落库）；`POST /v1/shadow/rollup`（及 `evalgate shadow rollup` CLI）才落一份 `shadow_reports` 快照并触发报警。生产用 cron 调 rollup，服务本身不内置定时器。

**Rationale**：
1. **后端薄 = 复用最大化**：observe 的 payload 正好是早就为 Phase 13 固化的 `EvalRecord` 契约（见 `core/schemas.py` 注释），滚动聚合直接喂 `gate.decision.build_gate_report`——shadow 与 PR CI **共用一套**四轴 + bootstrap CI + tag 归因 + `axis_breakdown` 子轴，零新统计代码。
2. **打分天然在调用侧**：SDK 已经为跑 candidate 持有 `PromptSpec` 和 LiteLLM 通道，就地打分省一次"把两段输出回传后端再调一次 judge"的往返，也不必在后端起一个能访问 prompt 配置的 judge worker。
3. **不背 scheduler 依赖**：1 人天的 phase 引入 APScheduler / 常驻 task 是过度工程；cron 调一个幂等 CLI 更符合"git-native / 配置外置"的项目调性（呼应 ADR-003），且 `compute_shadow_report` 是纯函数、易测。
4. **绝不阻塞主路径**：fire-and-forget + 1s 超时 + 吞异常，shadow 慢/挂都不影响生产请求——这是 shadow 能上生产的前提。

**Consequences**：
- 打分用的是 primary 的 judge 栈：candidate 若想换更严的 rubric 评，需要显式扩展（当前刻意从简，保证两边可比）。
- on-demand rollup 意味着"多久滚一次"是部署方的运维选择（cron 频率），服务不保证实时；报警延迟 = rollup 周期。
- SDK 把 LiteLLM judge 调用带进了调用方进程：成本/延迟落在后台 task（不阻塞主路径），但确实是调用方在掏这次 judge 的 token。
- 后台 task 需要强引用集合（`_BACKGROUND_TASKS`）防 GC——这是 asyncio fire-and-forget 的已知坑，已封装。
- 报警是自建的 Slack 兼容 webhook（`{"text": ...}` + 无 URL 时降级日志），没有引入 Slack SDK；未来要富文本 / 多通道再扩。
