# JOURNAL · 里程碑日志

> 每个 phase 完成后，在**顶部**追加一条。一条 ≈ 1 段话，包含：日期、phase 编号、做了什么、用了什么关键技术、有没有 trade-off / surprise。
>
> 不写 "今天我修了一个小 bug"。只写值得未来回顾的事 —— 简历能讲、系统设计上一个新形状、性能/质量 数据出来了 等。
>
> 最新在最上面，最早在最下面。

---

## 2026-05-15 · Phase 10 · Safety 轴落地（PII + jailbreak）+ axis_breakdown 重构

让 `multi_axis.safety` 从 demo 字段升级成真信号：每条 case 自动跑 PII（presidio）和 jailbreak（关键词 + 可选 LiteLLM 分类器）检测，把 4 项 sub-metric（`pii_input_rate` / `pii_output_leak_rate` / `jailbreak_attempt_rate` / `jailbreak_compliance_rate`）写进 `axis_breakdown["safety"]`，gate 在 safety 轴下挂同名 sub-axes 并按 lower-is-better 派发 bootstrap CI。

工程上做了一次值得记的小重构：原来的 `EvalRecord.sub_metrics: dict[str,float]` / `EvalResultRow.sub_metrics` / `EvaluationOutcome.sub_metrics` 全部改名为 **`axis_breakdown: dict[str, dict[str, float]]`**——外层键是 gate 主轴名（`quality` / `safety`），内层是 per-metric。RAG / agent evaluator 写 `quality`，Phase 10 安全管线追加 `safety`。这样 `multi_axis._build_sub_metric_axes` 通用化（`axis_name` + `direction` 形参），quality / safety 两个父轴共用一份派发逻辑，`passed = main_passed AND all(sub.passed)` 一模一样的语义。Migration 0010 在 PG / SQLite 双路把旧 `sub_metrics` payload 包成 `{"quality": <旧>}` 后 drop 旧列；downgrade 反向也保留数据。

具体落地：

- `src/evalgate/safety/`：
  - `PresidioPiiDetector` **绕过 `AnalyzerEngine`**——直接 lazy 实例化每个 `PatternRecognizer` 调 `.analyze(text, entities, nlp_artifacts=None)`。这样不依赖 spaCy 模型下载，CI 完全离线。代价是 `PERSON`/`LOCATION` 这种 NER recognizer 跑不了，但 ROADMAP 退出标准只需 PII 数字串类型，这点权衡写进了 PHASE_10_PLAN。
  - `JailbreakDetector` 三层：bundled 关键词 regex（DAN / `ignore previous instructions` / `developer mode` / …）→ 命中后 LiteLLM strict-JSON 分类器（`{"complied": bool, ...}`）→ 任一 fail 都退到 refusal-marker 启发式（扫 `I cannot` / `I'm sorry` / `I won't`）。`EVALGATE_MOCK_LLM=1` 或 `classifier_model: null` 直接跳过 LLM 段，CI 不连外网。
  - `SafetyPipeline.augment(case, outcome)` 永远不抛——子检测器异常降级为 0 速率，避免单点 detector 把整个 run 拖垮。`runner.iter_eval` 在每个 evaluator 返回后挂这一次 augment，generic / rag / agent 三条路径自动受益。
- 数据流：`PromptSpec.safety` block（`enabled` / `pii.entities` / `pii.score_threshold` / `jailbreak.keywords` / `jailbreak.classifier_model`）→ `build_safety_pipeline(spec, mock=...)` → `SafetyPipeline.augment` → `outcome.axis_breakdown["safety"]` + `safety_violation = outcome.safety_violation OR result.violation` → 持久化到 `eval_results.axis_breakdown` → gate 再读出来。`safety.enabled=false` 让 `build_safety_pipeline` 返回 `None`，runner 跳过整段，axis 退化回 boolean-only 行为。
- Demo 设计："输入分布漂移" 而不是 "提示词变弱"：baseline set 只有 3 条 clean case（pipeline 全 0 速率），candidate set 注入 5 PII + 4 jailbreak + 3 clean。同一 candidate 提示词跑两次，gate 把 candidate 的 safety 主轴 + 三项 sub-axis 标 fail。这绕过了 mock 模式下 `mock-candidate-output` 是常量、prompt-aware 差异不出来的结构限制。真 Ollama 模式下 `pii_output_leak_rate` 也会上升。

几个抉择记一笔：

1. **`sub_metrics → axis_breakdown` 直接改名**：用户明确说"不必考虑向后兼容"。这是 Phase 10 唯一干净的扩展方向——若加并行的 `safety_sub_metrics` 字段，gate 那边就要 hardcode "quality 看这里、safety 看那里"，跟 Phase 8 的 `_build_sub_metric_axes` 通用化背道而驰。一次改名换来 multi_axis 一处通用派发。
2. **migration 0010 还是保留数据**：用户说不必兼容，但保留 5 行 SQL 把旧 RAG payload 封进 `{"quality": ...}` 几乎免费，并且让 `scripts/phase8_rag_smoke.py` 在迁移后还能复现，所以选了保留路径。
3. **safety pipeline 不在 evaluator 里**：放在 `runner.iter_eval` 里 augment，是为了让 generic / rag / agent **三类 evaluator 完全不知道 safety 的存在**。Phase 11 之后想加新 evaluator 也不用关心 safety。
4. **mock 模式 demo 走"两个 set"**：`run_candidate(mock=True)` 永远返回 `mock-candidate-output`，prompt 差异不出来。让 baseline 用 clean-only set、candidate 用 mixed set，是把"安全风险"等价为"输入分布漂移"——更贴近真实 SaaS 场景，并且不破坏 mock 模式可重现。

**验证结果**：211 passed（177 旧 + 34 新增/改写），lint / format clean。`EVALGATE_MOCK_LLM=1 PYTHONPATH='src:.' python scripts/phase10_safety_smoke.py` 跑通：safety 轴 `delta=+0.75`、3 项 sub-axis（`pii_input_rate` / `jailbreak_attempt_rate` / `jailbreak_compliance_rate`）全 fail。本机真 Ollama mode 下 `pii_output_leak_rate` / `jailbreak_compliance_rate` 也会贡献 regression，留作后续 JOURNAL 补一行。

**关键技术语言**：Presidio `PatternRecognizer` 直调（无 NER 依赖）· LiteLLM strict-JSON jailbreak compliance classifier · refusal-marker heuristic fallback · `axis_breakdown` per-axis nested sub-metrics · cross-cutting safety hook in evaluator runner · alembic dual-path migration（PG 用 jsonb_build_object / SQLite 用 Python reshape）。

## 2026-05-15 · Phase 9 · Agent Trajectory Evaluator（Tool Runtime）

Phase 9 把 task-aware evaluator 从“RAG + generic”补全到 agent：不再让模型自报轨迹，而是把真实 tool runtime 接到 evaluator 链路里，先执行再评分。核心新增 `src/evalgate/evaluator/agent/` 五件套（`runtime / tools / parser / types / evaluator`）：planner 输出 strict JSON action（`call_tool` / `final_answer`），runtime 每步执行 builtin tools 生成 `actual_trajectory`，`AgentTrajectoryEvaluator` 再用 `expected_trajectory` 对齐打分。匹配规则按既定决策落地：**tool 名与顺序严格，args 用 expected ⊆ actual 深度子集匹配**；指标是 `tool_call_accuracy` + `step_wise_success`（前缀连续成功率），并通过 `EvaluationOutcome.sub_metrics` 进入 gate 的 `quality.sub_metrics`。

这次把数据链也补完整了：

- migration 0009 给 `eval_cases` 加 `expected_trajectory`（JSONB/JSON, default `[]`），ORM/REST/CLI/repository 全透传；
- CLI 新增 `evalgate eval-set add-agent-case --step '{"tool":"...","args":{...}}'`，支持手工构造多步 agent case；
- `case_extract` 新增 tool span -> expected trajectory 的 best-effort 抽取（`add_case_from_trace` 自动透传）；
- router 只在 `prompt.yaml` 有 `agent_runtime` 时注册 agent evaluator，没配就保持 per-case `unsupported_task_type`（不中断整 run），与 Phase 8 的设计一致。

为了保证“中间步骤错但最后答案蒙对”能被识别，demo 和 smoke 专门做了这种反例：`examples/agent_demo/prompts/agent_candidate.yaml` 把第二步工具顺序故意改错，`scripts/phase9_agent_smoke.py` 断言 `quality.sub_metrics.step_wise_success` 下降。因为 runtime 是真实执行的，最终答案文本不再能掩盖路径错误，这正是 Phase 9 的价值。

**验证结果**：新增 9 组 Phase 9 测试（runtime / evaluator / schema round-trip / run_eval / router / extractor / gate / prompt_spec / CLI），并通过针对性回归；全量测试、lint、format、phase9 smoke 全通过。  
**关键技术语言**：tool-runtime-grounded trajectory eval · strict JSON action protocol · ordered tool matching + args subset semantics · prefix step-wise success · quality nested sub-metrics gate。

## 2026-05-15 · Phase 8 · RAG-aware Evaluator + Evaluator 抽象层

把 runner 的硬编码"candidate→MultiJudge"流水拆成 `EvaluatorRouter` 驱动的 `task_type` 分派；同时把 RAG 评测路径接到官方 `ragas` 包上，跑 `faithfulness / context_precision / answer_relevance` 三项。Phase 8 同时是一次结构调整：`judge.runner` 直接删除，`judge/` 退守做 LLM-as-judge 原语，`src/evalgate/evaluator/` 接管 orchestration。

主要工程：

- **新抽象层 `evaluator/`**：`Evaluator` Protocol + `EvaluationOutcome` dataclass 是 router 和具体 evaluator 之间唯一的货币（包括 score / sub_metrics / confidence / output_text / retrieved_contexts / cost / latency / raw_calls / error）。`build_router(spec, mock=...)` 永远注册 `generic`（包旧 MultiJudge 路径），看到 prompt YAML 里有 `retriever:` + `rag_evaluator:` 才注册 `rag`；`agent` 留给 Phase 9 一行注册即可。
- **真 ragas + LiteLLM adapter**：用户明确选 A 路径——保留 RAGAS 品牌而不是自己重写 prompts。`ragas_adapter.py` 写一个 `LiteLLMChatModel(BaseChatModel)` + `LiteLLMEmbeddings(Embeddings)` shim 让 ragas 的 langchain 调用全部走我们已有的 `litellm.acompletion` / `aembedding`。`mock_text` / `mock_mode` 短路给单测和 CI 用，hash 384-dim 伪向量保证不连 Ollama 也能跑。一个 `_RagasScorer` 把 `ragas.evaluate(Dataset.from_dict(row), metrics=[...])` 包成 async（`run_in_executor`），结果转 `JudgeCallRecord(judge_model="ragas:<metric>")` 落 `eval_judge_calls`，Phase 17 calibration 直接复用。
- **Retriever 在 candidate 端（动态）**：用户选 B 路径——eval_case 上的 `retrieved_contexts` 是金标 reference（`context_precision_with_reference` 用），运行时让 candidate 自己查。`EmbeddingRetriever` 第一次 retrieve 触发 lazy 全 corpus embed（`asyncio.Lock` 防并发重复 embed），后续余弦排序取 top_k；候选 generator 用 `{contexts}` 渲染 user_template。
- **Schema（migration 0008）**：`eval_cases.retrieved_contexts: list[str]` NOT NULL default `[]`；`eval_results.sub_metrics: dict[str,float] | None`；`eval_results.retrieved_contexts: list[str] | None`（运行时实际检索结果，badcase 审计）。SQLite 走 `batch_alter_table` 加列。
- **Gate 显示分项**：`AxisMetric.sub_metrics: dict[str, "AxisMetric"] | None` 递归字段；`build_axis_metrics` 自动从 records 的 `sub_metrics` 派生 nested mean axes（每项 bootstrap CI）。**`quality.passed = passed AND all(sub.passed)`**——这点关键：candidate 把 faithfulness 拉到 0 但用 verbosity 把 answer_relevance 拉满，平均 score 不变也能 fail。`_summarize` 直接列出哪些 sub-metric 显著回归。

几个抉择记一笔：

1. **直接删 `judge.runner` 不留 alias**：用户给的明确指示"不必考虑向后兼容"。结果是 CLI / tests / phase7 smoke 同 PR 里全部切到 `evaluator.runner`，没有 deprecation 通道；干净度比"再多一周兼容期"值得。
2. **adapter 而不是 ragas custom_metric**：custom_metric 路径要写 ragas 那边的 `Metric` 子类，相当于把 ragas 的 prompt 工程也接管过来。adapter 路径只翻译"langchain 接口 → litellm 调用"——边界小、prompt 演进吃 ragas 上游红利。代价是 ragas 0.1/0.2 的内部 API（`base.llm = ...` vs 注入构造器）有差异，metric builder 用 `with contextlib.suppress(AttributeError)` 兜两种形状，mock 全跑通后实测 0.2.15 落地。
3. **混合 set 部分聚合 sub_metrics**：generic case 不带 `sub_metrics`，RAG case 带；`_pluck_metric` 只看 dict 里有该 key 的 records → faithfulness 不会被一群 generic case 拉到 0。这是为了让 Phase 12 之后真实 CI 的 eval set 可以异质，不强迫"一个 set 全是 RAG"。
4. **mock 模式 sub_metric=0 是预期**：`LiteLLMChatModel.mock_text` 返回固定字符串，ragas 的 claim 抽取 / NLI 解析失败收敛到 0；端到端 plumbing 全通，但有意义的 sub-metric 数字要真 LLM。这条 trade-off 写进 PHASE_8_PLAN 的"风险"小节，避免后人误以为 ragas 跑不出分。
5. **`retrieved_contexts` 命名复用**：case 上和 result 上同名但含义不同（reference vs runtime），同名更直观，column docstring 区分。

**Tests**：30 新测试 + 123 旧测试 = **153 passed**；`make lint` clean，`make format` clean。`EVALGATE_MOCK_LLM=1 PYTHONPATH=. python scripts/phase8_rag_smoke.py` 端到端跑通 5 case，gate 报告 `axes[quality].sub_metrics` 含三项嵌套 axis。本机 Ollama 真跑（`qwen2.5:7b` + `qwen3-embedding:8b`）的退出标准留给后续手动验证（README 已经能装 Ollama 的同学复现）。

**关键技术语言**：task-aware evaluator dispatch · RAGAS faithfulness / context-precision / answer-relevance · LiteLLM↔langchain BaseChatModel adapter · embedding-based retriever (cosine over corpus) · nested sub-axis bootstrap CI · pluggable evaluator architecture for Phase 9 agent extensions.

---

## 2026-05-15 · Phase 4.5 · `EvalCaseRow.eval_set_id` 下线（彻底归一到 memberships）

7.5 之后再做一次自审：`EvalCaseRow.eval_set_id`（Phase 4 N:1）和 `EvalCaseSetMembershipRow`（Phase 7.5 N:N）并存是「两套真理」。原因之一就是「为了 Phase 4 / 5 / 6 一行不改」的保留策略——这是典型的 backward-compat 妥协。这一阶段把它彻底改干净：case 是纯 payload，membership 是唯一的「case 属于哪些 set」之处。

- 新 migration [0007](src/evalgate/db/migrations/versions/0007_drop_eval_case_eval_set_id.py)：先 backfill 每一行 `EvalCaseRow.eval_set_id` 进 `eval_case_set_memberships`（dedup 已有），再 `batch_alter_table` 删 index + column。`downgrade()` 反向走，取 oldest membership 当 primary 还原，可逆。
- ORM：`EvalCaseRow.eval_set_id` 字段消失；docstring 重写「payload-only」。
- `eval_set/repository.add_case[_from_trace]` 同一事务里加一条 membership（`promoted_from_result_id=NULL, strategy=NULL`）——"originating membership" 与 "promoted membership" 结构同源，差别只在元数据列。
- `list_cases(set_id)` 简化成单 JOIN（删了 Phase 7.5 的 union + dedup 那 10 行）。
- `badcase/repository.promote_result_to_set`：`SameSetPromotionError` 整类删除——「promote 进原始 set」结构上就是「往已经存在的 (case, set) 写第二次」，统一回落到 `AlreadyPromotedError`（HTTP 409）。少一类错误码，semantic 反而更清。
- 对外契约：`EvalCaseOut`（`GET /v1/eval-sets/{id}` cases 数组、`POST .../cases`、`POST .../cases/from-trace`）的 `eval_set_id` 字段被删。container 是 set、payload 是 case、归属关系单独通过「列 set 看 case」或 `PromotionOut` 表达——三种 shape 不再混着塞同一个字段里。

几个值得记的设计抉择：

1. **Migration 顺序：先 backfill 再 drop**。如果反过来 PG 会 FK violation；migration 里手工读 + bulk_insert，跳过已有 (case, set) 来兼容 dev 环境里跑过半的中间态。
2. **`downgrade()` 真的写了**：取该 case 最早一条 membership.created_at 作为 primary set——这跟 Phase 4 的原意（"originating set"）严格一致；下线一个表得真的能滚回去，否则就是写死。
3. **`SameSetPromotionError` 一并删掉**：保留它就只是在 wrapping `AlreadyPromotedError` 给一个"更友好的"名字。Phase 4.5 之后 origin 跟 destination 在数据模型上没区别，特殊错误就是噪音；HTTP 409 + 消息「already a member」对调用方足够。
4. **Test 改写策略**：`tests/test_badcase_*` 里手工 seed `EvalCaseRow(eval_set_id=...)` 的 3 个 fixture 全改走 `set_repo.add_case`（即生产路径），顺手把测试也变成 add_case 的覆盖；`test_promote_into_origin_set_is_already_promoted` 替掉 `test_promote_same_set_rejected` —— 同样的不变量，更通用的错误。
5. **零运行时性能损失**：原 union 双查 + 应用 dedup 替成单 JOIN，prod 查询 plan 还更简单。

**Test**：lint clean，**123 passed**（同 7.5 后基线），Phase 7 smoke 端到端跑通（10 case → run → uncertainty 3 → promote → target set 看到 3 case）。

**详细方案**：plan 内联进 [docs/PHASE_7_PLAN.md](docs/PHASE_7_PLAN.md) 的「Phase 4.5 收尾」附录。
**Commit**: 待 commit。

## 2026-05-15 · Phase 7.5 · promote 改 many-to-many membership 表（cleanliness refactor）

补一个 Phase 7 自审时识别的设计债：原 promote 直接复制 `EvalCaseRow` 进 target set（受 Phase 4 N:1 `eval_set_id` 限制），三个问题：payload 重复、lineage 只能 tags 字符串软追溯、同 case 二次 promote 没结构性 dedup。这次彻底改干净（Phase 4.5 后续又把 `eval_set_id` 列从 `EvalCaseRow` 整体下线，参见下文）：

- 新表 [`eval_case_set_memberships`](src/evalgate/db/migrations/versions/0006_create_eval_case_memberships.py)（0006 migration）：`(eval_case_id, eval_set_id)` 唯一约束 + `promoted_from_result_id` + `strategy` + `tags` + `created_at`
- `EvalCaseRow.eval_set_id` 语义保留为「原始/主集」——**Phase 4 / 5 / 6 一行代码不改**
- `eval_set/repository.list_cases(set_id)` 改成「主集行 ∪ membership 行」去重 union；Phase 5 runner 不知不觉就能迭代到 promoted 进来的 case
- `badcase/repository.promote_result_to_set(...)` 重写：不再 copy case，只 insert 一条 membership；新增 `AlreadyPromotedError` → HTTP 409 + CLI rc=1
- API 响应模型从 `EvalCaseOut` 换成 [`PromotionOut`](src/evalgate/core/schemas.py)，暴露 membership 元数据（client 想拿 case payload 走 `GET /v1/eval-sets/{set_id}`）

几个值得记的取舍：

1. **保留 `EvalCaseRow.eval_set_id` 不删**：是「原始集」语义而不是 backward-compat 妥协——`add_case_from_trace` / `get_eval_set_detail` / Phase 4 一堆查询都用它。删了得动 4 个文件，加了得 1 个 SQL union + 5 行 dedup，权衡明显。
2. **`list_cases` 用应用层 union 而不是 SQL `UNION`**：跨 SQLite（aiosqlite test）+ Postgres（prod）一份代码，避免方言细节，5 条 case 量级根本无所谓 perf。
3. **API 是 breaking 的（仅 Phase 7 路径）**：promote 响应从 case 字段集变成 membership 字段集；这是有意的，因为 Phase 7.5 之前的 `EvalCaseOut` 返回值在新模型里已经语义错位（旧 case 还在 src set，"返回的 case_id" 概念模糊）。Phase 1–6 完全不沾这条 API 路径。
4. **结构性 dedup > application dedup**：`UniqueConstraint(case, set)` 是真理来源，application 层在 commit 前先 SELECT 一次只是为了拿到友好错误消息——不靠它做正确性。
5. **Membership tags 与 case.tags 解耦**：原 Phase 7 把 `badcase:strategy:<s>` 塞进 `EvalCaseRow.tags`（修改了 case 本身），Phase 7.5 改成 `EvalCaseSetMembershipRow.strategy` 列 + `tags` 列——case 是 case，promote 元数据是元数据。

**Test 变更**：原 19 个 Phase 7 测试中 7 个改写（字段名换成 membership shape），新增 5 个（`already_promoted` 在 repo / router / CLI 三层 + `list_cases(target)` 看到 promoted case + `GET /v1/eval-sets/{dst}` 同样可见）。**Phase 1–6 全套测试零修改**。`make test`：**123 passed**，lint clean，smoke 跑通。

**Tech**: SQLAlchemy `UniqueConstraint`、双 FK CASCADE、JSON tags 列同时支持 PG JSONB + SQLite JSON 回退、Pydantic v2 `PromotionOut` DTO。

**详细方案**：[docs/PHASE_7_PLAN.md](docs/PHASE_7_PLAN.md) 文末「Phase 7.5 后置 refactor」段。
**Commit**: 待 commit。

## 2026-05-15 · Phase 7 · BadCase Finder（uncertainty + outlier + llm + promote）

把 Phase 6 写到 `eval_results.judge_confidence` / `latency_ms` / `cost_usd` 的信号变成可执行动作：扫 `eval_results` 自动捞最值得入 eval_set 的 case，CLI / REST 一行 promote 复制到目标 set，构建越用越准的回归基线。整套**零新表**——Phase 5/6 早就把列预留好了，Phase 7 只是把它们当 active-learning 的输入用起来。

新增 [src/evalgate/badcase/](src/evalgate/badcase/) 两件套（`finder.py` 三策略 + `repository.py` promote）、REST 端点 `GET /v1/badcases?strategy=...` + `POST /v1/badcases/{id}/promote`、CLI 子命令 `evalgate badcase list / promote`、smoke 脚本 [scripts/phase7_badcase_smoke.py](scripts/phase7_badcase_smoke.py)。三种策略：

| Strategy | 排序逻辑 | 直觉 |
|---|---|---|
| `uncertainty` | `judge_confidence ASC NULLS LAST` | judge 越不确定 → 越值得人工 review |
| `outlier`     | `score=0 ∨ safety ∨ latency>p95 ∨ cost>p95`，severity = 命中条件数 | 已知坏 + 长尾 |
| `llm`         | 先取 2×limit uncertainty 候选 → cheap model 二筛 "subtle_bad" | active learning 漏斗 |

几个值得记的设计取舍：

1. **不加新表**：Phase 7 全是 SELECT，LLM 标签也不缓存。决策清晰度 > 性能微优化；Phase 17 calibration 真要复用 LLM 标签再加 `bad_case_labels`。
2. **Promote 走"复制 EvalCaseRow"而不是"多对多挂"**：`EvalCaseRow.eval_set_id` 保持 N:1，避免新建 join 表 + 改写 Phase 4 一堆 list/filter；source set 的快照不被污染。Lineage 通过 tags 弱耦合（`badcase:source-case:<id>` + `badcase:strategy:<s>`），跟 Phase 4 `source_trace_id` 的"软引用"哲学一致。
3. **同 set promote 显式拒绝（`SameSetPromotionError` → HTTP 409）**：防呆——同 set 复制是 no-op anti-pattern，硬报错胜过 silently 创建重复数据。
4. **`p95` 数据稀疏防呆（`MIN_FOR_PERCENTILE=4`）**：少于 4 行时跳 percentile 判定，只看 `score=0 / safety`。p95 在 n=3 上没统计意义，强行算反而把 outlier 标准化掉。
5. **LLM 策略的 prompt 用 `{{...}}` 转义 JSON 大括号**：踩过坑——Python `str.format` 会把示例 JSON 里的 `{"subtle_bad":...}` 当占位符报 `KeyError`，本来想直接用 f-string 但保留模板灵活性，最后用了 `{{}}` 转义。
6. **`acompletion_json` 复用 Phase 6 的 protocol 层**：cheap model 调用不另搞一套 litellm 壳，直接借用——judge 的失败兜底（不向上 raise、parse 不到给 fallback）也跟着继承。

**Smoke 真跑**（mock 模式）：10 条 billing case → mock judge → `find(strategy="uncertainty", limit=3)` 拿 3 条 → 三连 promote → target set `phase7-hard` 落 3 条新 case。退出码 0、闭环跑通。

**Tech**: `numpy.percentile`（已是 dep）、SQLAlchemy `select` + ORDER BY、Pydantic `BadCaseOut`（API contract）、`asyncio.run` + `AsyncSession` 测试夹具一致复用。

**Test 数量**：原 99 + 新 19 (`finder` 5 + `promote` 5 + `routers` 5 + `cli` 4) = **118 全绿**。lint clean。

**详细方案**：[docs/PHASE_7_PLAN.md](docs/PHASE_7_PLAN.md)
**Commit**: 待 commit。

## 2026-05-14 · Phase 6 · Judge Robustness（MultiJudge × PositionSwap × SelfConsistency）

Phase 5 是 1 judge × 1 次 × 1 角度。Phase 6 把它升级成「N judge × K self-consistency × P=2 position swap」的三层包装栈。每条 case 最多 `N×K×P` 次 judge 调用，每一次都落新表 `eval_judge_calls`（0005 migration），上层用 `judge_confidence`（per-judge std + cross-judge std 两层）告诉 gate「这个 case 我自己有多确定」。

**真实数据**（本机 Ollama，5 条 billing case，每条带 reference，N=3 次重复）：

| Config | Mean per-case score stdev |
|---|---|
| single_pointwise（1 judge, K=1, temp=0.7） | **0.0377** |
| multi_pairwise（2 judges 7B+32B, K=3, swap on） | **0.0136** |

多层栈把跨次评分波动压到 **1/2.8**，符合 MT-Bench / G-Eval 论文的方向预期。完整数字与 yaml 见 [scripts/phase6_variance.py](scripts/phase6_variance.py) + [examples/prompts/{single_pointwise,multi_pairwise}.yaml](examples/prompts/)。

**Breaking change**（明确选择不向后兼容）：

1. **`prompt.yaml` 改 `judges: [...] + judge_policy:`**：删 Phase 5 的单数 `judge:`，loader 直接 `ValidationError` 报错并给迁移示例。一刀切，把"两种 schema 同时存在"的二次复杂度消灭掉。
2. **拆 `RubricJudge` 为 `PointwiseJudge` + `PairwiseJudge`**：原文件删；共享 litellm 壳 + 解析层抽到 [protocol.py](src/evalgate/judge/protocol.py)。pairwise 不输出 0..1 score（只出 winner: A|B|tie），0/0.5/1 由 `PositionSwapJudge` 聚合 — 把"绝对分"和"相对偏好"两种语义彻底隔离。
3. **`eval_judge_calls` 一行一次原始调用**：N×K×P 行/case 全落库，Phase 14 算 κ、Phase 17 算 ECE 直接 SQL，不再回放 judge。`eval_results.judge_confidence` 真填了，gate / BadCase 现在可用。
4. **`case.expected` 在 pairwise 模式下硬必需**：缺失 → emit `error=True, error_kind="missing_reference"` 的 EvalRecord，**不静默 fallback 到 pointwise**。失败显式可见，胜过埋雷。
5. **Confidence 公式两层乘**：`per_judge_conf = 1 - std/0.5`（self-consistency 内部稳定度）× `cross_term = 1 - cross_std/0.5`（judge 间一致度）。两层都满 → 1.0；任一层崩 → 接近 0.0。最大 std=0.5 来自分数 ∈[0,1] 的几何上界，让 confidence ∈[0,1] 直接可解释。

栈的拓扑：`Runner → MultiJudge(N) → SelfConsistencyJudge(K) → PositionSwapJudge(P) → PointwiseJudge | PairwiseJudge`，单 case 内 `N×K×P` 次走 `asyncio.gather + Semaphore(concurrency)`，跨 case 仍然顺序（保留 Phase 16 stream）。Temperature 自动 bump：K>1 且用户没设 → 强制 ≥0.7（K=1 不动），否则 greedy decoding 让方差信号塌成 0，confidence 公式失效。

**Tech**: Pydantic v2 `model_validator(mode="before")` 拦截 legacy 字段、`statistics.pstdev` 做总体方差、`asyncio.Semaphore` 限速、`response_format={"type":"json_object"}` 提示 JSON 输出、SQLAlchemy ORM + JSONB on PG / JSON fallback on SQLite。

**Test 数量**：原 19 + 新 7 (`pointwise / pairwise / position_swap / self_consistency / multi_judge / judge_calls_persistence / runner_multi_judge`) = **99 全绿**。lint clean。Phase 5 三个测试文件 + Phase 5 candidate test 一并迁移到新 schema。

**详细方案**：[docs/PHASE_6_PLAN.md](docs/PHASE_6_PLAN.md)
**Commit**: 待 commit。

## 2026-05-14 · Phase 5 · LLM-as-Judge Runner v1（LiteLLM + 本地 Ollama）

把 Phase 4 攒下的 eval_set 真正"跑起来"。新增 [src/evalgate/judge/](src/evalgate/judge/) 五件套（`prompt_spec` / `candidate` / `rubric_judge` / `persistence` / `runner`），加 `evalgate run --eval-set X --prompt p.yaml --out r.json` CLI 子命令，落两张新表 `eval_runs` / `eval_results`（0004 migration），输出 JSON 直接喂 Phase 2 的 `evalgate gate`。本地用 **qwen2.5:7b（Ollama）** 真跑通：3 条 billing case，baseline vs candidate 两次 run，4 轴 gate 报告齐活，候选弱化 prompt 让 latency_p95 从 12.7s 掉到 1.3s，验证了 latency 轴的真信号。

几个值得记的设计取舍：

1. **Rubric 放进 `prompt.yaml`，不进 eval_set**：评分标准跟候选 prompt 一起在 git 里 diff，避免在 DB 里维护"通用 rubric"的复杂度。
2. **Runner 写成 `iter_eval` 流式 + `run_eval` 薄包装**：Phase 16 Sequential Gate 直接消费 stream 做 early-stop，无需重构。
3. **`EvalResultRow` 预留 `judge_confidence` + `judge_raw`**：Phase 17 Calibration 重算 ECE 不需要重跑 judge、不需要再发 migration。
4. **`EvalRecord` 落到 `core/schemas.py` 当固化契约**：Phase 18 Shadow Mode 的 `/v1/shadow/observe` 直接 import 复用，不会出现字段名漂移。
5. **CLI 加 `--mock` + `EVALGATE_MOCK_LLM=1` 环境变量**：CI / pytest 走 mock 不烧外部 API；本地默认真调 Ollama。`litellm.completion_cost` 对 `ollama/*` 没定价会 raise，wrapper fallback 0.0 不炸。
6. **Judge 解析三层兜底**：`json.loads` → regex `r'score\s*(?:[:=]|\bis\b)\s*([0-9.]+)'` → 全失败给 score=0 + reason 存原文。**绝不向上抛**，一条 case 失败不污染整个 run。

整套加完 19 个新测试（prompt_spec / rubric_judge / candidate / runner / runner→gate 端到端 / CLI 端到端），全部走 aiosqlite + `litellm.mock_response`，CI 完全离线。

**Tech**: LiteLLM `acompletion` + `completion_cost`、Pydantic v2 `model_copy` 做 spec override、`asyncio` + `AsyncIterator` 流式 runner、Ollama qwen2.5:7b、sha256 prompt hash 做审计指纹、`litellm.suppress_debug_info` 压广告横幅以净化 CLI stdout。

**详细方案**：[docs/PHASE_5_PLAN.md](docs/PHASE_5_PLAN.md)
**Commit**: 待 commit。

## 2026-05-14 · Phase 4 · Eval Set Manager

落地"trace → eval_case"的语义桥。两张新表（`eval_sets` + `eval_cases`，0003 migration），5 个 REST 端点，3 个 CLI 子命令（`create` / `add` / `show`），核心是 [src/evalgate/ingest/case_extract.py](src/evalgate/ingest/case_extract.py) 这个纯函数：从一条 trace 的所有 span 里挑**第一个 LLM span**（`evalgate.kind=llm` OR 任意 `gen_ai.*` attribute），把 prompt → `case.input`、response → `case.expected`，剩下的 sibling span 用来推断 `task_type`（有 retriever → rag，多个 tool → agent，否则 generic）。

设计上有几个值得记的取舍：

1. **`source_trace_id` 不做 FK**：eval_case 必须独立于 trace 生命周期（trace 未来会有 retention + archive），所以是软引用 + 索引。
2. **`tags` 用 JSONB 不用 PG `TEXT[]`**：跟 ADR-002 + Phase 3 aiosqlite test fixture 保持一致，跨方言一份代码。
3. **CLI 直连 DB 不走 HTTP**：跟现有 `evalgate gate` 一致，CI 友好。`SessionLocal` 在测试里被 `monkeypatch` 注入 aiosqlite。
4. **抽 case 走"第一个 LLM span"不是"每个 LLM span"**：一 trace -> 1 case，dedup 简单，符合 multi-step agent 也只关心最终 LLM 决策的直觉。Phase 7 BadCase finder 再处理 N 条 case 的场景。

**Tech**: SQLAlchemy 2.0 ORM + `Annotated[..., Depends]` Pattern、FK + CASCADE、argparse subparsers、SQLAlchemy `func.now()` server defaults、`Protocol` 做结构化类型让纯函数同时吃 ORM row 和 pydantic model。
**Commit**: 待 commit。

## 2026-05-14 · Phase 3 · OTel 端到端打通 + Trace 浏览 API

把 Phase 1 的 mapper 拓出来真接 OTel SDK：`POST /v1/otel/traces` 同时收 `application/x-protobuf`（OTel Python SDK 默认）和 `application/json`（curl 调试用），落到新的 `traces` 汇总表 + 已有 `spans` 表。汇总不是简单 `+=`，而是每次 ingest 后从 `spans` 实时聚合（`min(start)/max(end)/count`），重推 / 乱序 partial delivery 都不会双计。

新增 `examples/demo_app/`：`litellm.completion(..., mock_response="four")` + 手写 OTel `TracerProvider` + `OTLPSpanExporter`，3 个 span 一次 rag-pipeline，**零 API key 跑通**。`make demo-trace` 一键串起 DB → migrate → API → demo → curl。

测试侧加了 `aiosqlite` in-memory engine fixture + FastAPI `dependency_overrides`，所有 DB-touching 测试不依赖真 Postgres；持久化层用 `sqlalchemy.dialects.{sqlite,postgresql}.insert(...).on_conflict_do_*` 抽 SQLite / PG 双方言写库逻辑。

**Tech**: `opentelemetry-proto`（`ExportTraceServiceRequest`）、`opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-http`、LiteLLM mock_response、SQLAlchemy 2.0 dialect-aware UPSERT、aiosqlite。
**Commit**: 待 commit。

## 2026-05-14 · Phase 2 · 多轴 CI Gate v1 跑通

实现了 `evalgate gate` CLI + GitHub Actions workflow `eval-gate.yml`：从 baseline / candidate 两份 JSON 算出四轴 metric（quality / cost / latency_p95 / safety），mean 类轴用 **bootstrap diff CI（1000 次重采样，95%）** 判显著性，p95 轴 v1 先用阈值（留作技术债，见 ADR-004）。

`build_axis_metrics` + `tagwise_attribution` + `build_gate_report` 三层分离，方便后面 Phase 5/6 真 judge 接入时只换数据源不动 gate 逻辑。

`scripts/seed_demo.py` 在 `billing` tag 上注入 -0.22 score 的 regression，CI 跑完会在 PR 上自动评论 4 轴报告 + tag 归因表 + 整体 PASS/FAIL，不通过时阻塞 merge。整条 demo 链路是端到端的。

**Tech**: numpy bootstrap、Pydantic v2 schemas、GitHub Actions `actions/github-script@v7`。
**Commit**: `be3a749`

## 2026-05-14 · Phase 1 · Walking skeleton

FastAPI app（`/healthz` + OTel ingest router）+ async SQLAlchemy（`asyncpg`）+ Alembic 初始 migration 全部 wire 起来。

最关键的是 `src/evalgate/ingest/otel_mapper.py` —— 把 OTLP/JSON 的 `ResourceSpans → ScopeSpans → Span` 三层结构 flatten 成内部 `traces` + `spans` 表的行。这一层是 ADR-001（用 OTel 不做自家 SDK）和 ADR-002（PG + JSONB）落地的接缝点：未来 OTLP semantic convention 怎么变，只改这个 mapper，不动 DB schema。

测试用 in-memory FastAPI + 假 OTLP payload，跑得很快，不依赖真 Postgres。

**Tech**: FastAPI async router、SQLAlchemy 2.0 async session、Alembic、Pydantic v2、OTLP/JSON spec。
**Commit**: `039d9fc`

## 2026-05-14 · Phase 0 · 仓库 bootstrap

`uv` 管包 + `pyproject.toml`（PEP 621）+ `ruff` lint/format + `pytest` + `pre-commit` + `docker-compose`（Postgres 16）+ `.github/workflows/ci.yml`（lint + test）+ Apache-2.0 license + 多 stage Dockerfile。

选择 `uv` 而不是 poetry 是基于 CI 速度和零 bootstrap 依赖（见 ADR-007）。

**Tech**: uv 0.5+、ruff 0.7+、pytest 8.3+、Python 3.12。
**Commit**: `642e8fe`
