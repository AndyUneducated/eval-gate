# Phase 7 技术方案 · BadCase Finder（uncertainty + outlier + llm）

> 对应 [ROADMAP.md](ROADMAP.md) Phase 7。预估 1 人天 vibe coding。
> 完成后只更新顶部状态行 + 在 [JOURNAL.md](../JOURNAL.md) 记里程碑。

**状态**：DONE（含 Phase 7.5 refactor：promote 改 membership 表，见文末「Phase 7.5 后置 refactor」）。数据源选 A（仅 `eval_results`）。

---

## 一句话

跑完 evalgate run 之后，从 `eval_results` 里**自动捞出最值得加进 eval_set 的 case**：判官最不确定的（uncertainty）、跑得最贵 / 最慢的（outlier）、便宜模型一眼能看出"微妙错误"的（llm）。捞出来一行 `evalgate badcase promote ...` 复制到目标 eval_set，丰富 regression 基线。

## 数据源决策（**需要用户拍板**）

**A）只看 `eval_results`（推荐 MVP）**：所有策略都基于"已经被 judge 跑过的 case"，依赖 Phase 5/6 写好的 `judge_confidence` / `latency_ms` / `cost_usd` / `safety_violation` 列。优点：单一表、SQL 简单、跟 ROADMAP 里"必须先有 Phase 5/6 confidence 数据"一致；缺点：raw trace 里 latency/cost outlier 没被覆盖。

**B）`eval_results` + 原始 `spans` 双源**：再加一种 `trace_outlier` 策略，扫 `spans.attributes` 找 latency/cost > p95 的未评测 trace，走 `case_extract.py` 提取 → 入候选池。优点：发现 production 里"完全没进 eval_set 的异常"；缺点：多一条路径、`uncertainty` 不适用（trace 还没跑 judge）、跟现有 `eval-set add --from-trace` 功能重叠。

> **我的建议：A**。Phase 7 是闭环 evals → 主动学习的第一步，先把 eval_results 里的信号榨干；trace-source outlier 留给后续小迭代（或独立 phase），避免一开始就把两条路径都做半成品。

## Schema（不加新表）

Phase 5/6 的 `eval_results` 已经齐了 4 个信号列（score / judge_confidence / latency_ms / cost_usd / safety_violation），Phase 7 **不发 migration**，全部查询 on-the-fly。

LLM 策略每次重跑也行（cheap model，调一次 ~$0），如果以后嫌慢，Phase 17 calibration 顺手加 `bad_case_labels` 缓存表。**Phase 7 MVP 不引入缓存表**——决策清晰度优先。

## 核心 API：BadCaseFinder

[src/evalgate/badcase/finder.py](../src/evalgate/badcase/finder.py)：

```python
@dataclass
class BadCase:
    eval_result_id: str
    eval_case_id: str | None
    eval_run_id: str
    score: float
    judge_confidence: float | None
    latency_ms: int
    cost_usd: float
    safety_violation: bool
    tags: list[str]
    strategy: str            # "uncertainty" | "outlier" | "llm"
    reason: str              # human-readable 一句话
    llm_label: dict | None   # 仅 llm 策略填

async def find_uncertainty(session, *, run_id: str | None, limit: int) -> list[BadCase]: ...
async def find_outlier(    session, *, run_id: str | None, limit: int) -> list[BadCase]: ...
async def find_llm(        session, *, run_id: str | None, limit: int,
                            cheap_model: str = "ollama/qwen2.5:7b",
                            mock: bool = False) -> list[BadCase]: ...
async def find(            session, *, strategy: str, ...) -> list[BadCase]:
    """Dispatch by strategy."""
```

排序规则：

| Strategy | SQL / 逻辑 | 直觉 |
|---|---|---|
| `uncertainty` | `ORDER BY judge_confidence ASC NULLS LAST` | judge 越不确定越有研究价值 |
| `outlier` | `score=0` OR `safety_violation=True` OR `latency_ms > p95` OR `cost_usd > p95` | 已知坏 + 长尾 |
| `llm` | 先取 top-2*limit by uncertainty → 让 cheap model 给"subtle bad" 评 0/1 → 留 1 的取 limit | active learning |

> **p95**：在 run 内算（query 带 `run_id`）或全局（不带 run_id）。用 `numpy.percentile`（已是 dep）。

## REST

[src/evalgate/api/routers/badcase.py](../src/evalgate/api/routers/badcase.py)：

- `GET /v1/badcases?strategy=uncertainty&limit=20&run_id=<uuid>` → `list[BadCase]`
- `POST /v1/badcases/{eval_result_id}/promote` body `{target_set_id, extra_tags}` → 新建 `EvalCaseRow` in target set，返回 `EvalCaseOut`

挂在 [src/evalgate/api/main.py](../src/evalgate/api/main.py) `app.include_router(badcase.router, prefix="/v1", tags=["badcase"])`.

## CLI

[src/evalgate/cli.py](../src/evalgate/cli.py) 加 `badcase` 子命令组：

```
evalgate badcase list   --strategy {uncertainty|outlier|llm} [--limit 20]
                        [--run <run_id>] [--mock]
evalgate badcase promote --result <eval_result_id>
                         --eval-set <target_set_id_or_name>
                         [--tag interesting --tag from-phase7]
```

`list` 输出 JSON 数组（含 `eval_result_id`，下一步 promote 用）。`promote` 复制 `eval_case` 的 `input/expected/task_type` 到 target set，自动追加 `tags=["from-badcase","strategy:<s>"] + 用户额外 tags`，并保留 `source_trace_id`/`source_span_id` 让 lineage 追得回去。

## Promote 语义（关键设计）

[src/evalgate/badcase/repository.py](../src/evalgate/badcase/repository.py)：`promote_result_to_set(session, *, result_id, target_set_id, extra_tags) -> EvalCaseRow`。

- 输入：`eval_result_id`（badcase finder 返回的那个）
- 解析：load `EvalResultRow` → 它的 `eval_case_id` → load `EvalCaseRow`
- 写入：在 `target_set_id` 里**新建**一条 `EvalCaseRow`，input/expected/task_type 全部复制，tags 合并（去重），`source_trace_id`/`source_span_id` 透传
- 不允许 promote 到原 set（同 set 的 dedup 防呆，HTTP 409 / CLI 报错）

> **不做 hard reference**：新 case 跟原 case 之间**不建外键**，只通过 tags 弱耦合（`tags` 加 `"badcase:from:<original_case_id>"`）。理由跟 Phase 4 `source_trace_id` 一致——eval_case 必须能独立删除/归档。

## LLM 策略细节

cheap model 提示：

```text
Given an LLM input and its candidate output, decide whether the output is
SUBTLY WRONG (correct-looking but inaccurate / unhelpful / off-spec).
Return STRICT JSON: {"subtle_bad": true|false, "reason": "<one sentence>"}.

INPUT:
{case_input}

OUTPUT:
{candidate_output}
```

实现复用 [src/evalgate/judge/protocol.py](../src/evalgate/judge/protocol.py) 的 `acompletion_json`。`mock_response` 用 `'{"subtle_bad": true, "reason": "mock"}'` 让 CI 走得通。

## 测试

- [tests/test_badcase_finder.py](../tests/test_badcase_finder.py)：seed 一个 run + 多条 eval_results（手动设置 confidence/latency/cost），断言三种策略排序
- [tests/test_badcase_promote.py](../tests/test_badcase_promote.py)：promote 后 target set 多一条 case，input/expected/tags 正确合并；同 set 拒绝
- [tests/test_badcase_routers.py](../tests/test_badcase_routers.py)：REST 端到端
- [tests/test_badcase_cli.py](../tests/test_badcase_cli.py)：CLI list + promote 闭环

## Smoke 脚本

[scripts/phase7_badcase_smoke.py](../scripts/phase7_badcase_smoke.py)：

1. 临时 SQLite，seed 10 条 billing case
2. 跑 `evalgate run --mock`（让 judge_confidence 由 SC mock 数据带出来）
3. 调 `BadCaseFinder.find(strategy="uncertainty")` 拿 top-3
4. 调 `promote_result_to_set` 复制到新 eval_set "billing-hard"
5. 打印结果

走 `EVALGATE_MOCK_LLM=1` 完全离线可重现。

## 退出标准

- 全测试绿（旧 99 + 新 ~12 ≈ 111）
- `ruff check` clean
- `scripts/phase7_badcase_smoke.py`：mock 模式跑通，10 条 → 3 badcase → promote 后 target set count + 3
- ROADMAP Phase 7 → `[DONE]`
- 数字 + commit hash 写 JOURNAL

## 风险点 / 范围控制

- **不做**：trace-only outlier（B 方案）、LLM 标签持久化、自动 promote（必须用户手动 `promote` 才入 set）、UI / 可视化
- **依赖**：`numpy.percentile`（已是 dep）；`litellm.acompletion`（Phase 5 引入）
- **数据稀疏时**：少于 limit 条记录就全返；少于 4 条记录跳 p95（直接全部当 outlier 也行；返回 0 行）

## Forward-compat

- Phase 14（κ 实验）：直接消费 promote 出来的 hard cases，跟人标对照
- Phase 15（Adversarial Case Synth）：BadCaseFinder.find_llm() 的 prompt 可以替换成"生成更难的变体"，重用同一管线
- Phase 17（Calibration）：补 `bad_case_labels` 缓存表，避免重复 LLM 调用

---

## Phase 7.5 后置 refactor · promote 改 many-to-many membership

**动机**：原 Phase 7 promote = 复制 `EvalCaseRow`（不同 `id`、相同 payload）。受 Phase 4 `eval_cases.eval_set_id` N:1 约束。问题：

1. 同一 case 进多个 set 要复制 N 份 input/expected → 编辑/版本管理麻烦
2. lineage 只靠 tags 字符串（`badcase:source-case:<id>`）软追溯，没法 SQL JOIN
3. 防止「同 case 二次 promote」需要 application-level 查重，结构上没保护

**新设计**：

- 新表 `eval_case_set_memberships(id, eval_case_id, eval_set_id, promoted_from_result_id, strategy, tags, created_at)` + `UniqueConstraint(case, set)`
- `EvalCaseRow.eval_set_id` 保留为「原始/主集」语义，Phase 4 一切代码不动
- `list_cases(set_id)` 改为「主集行 ∪ membership 行」去重，Phase 5 runner 无需感知
- `promote_result_to_set(...)` 不再复制 case，只 insert 一条 membership，结构性 dedup
- API 响应模型从 `EvalCaseOut` 换成新 `PromotionOut`（暴露 membership 元数据：`promoted_from_result_id`、`strategy`、`tags`、`created_at`）
- 新增 `AlreadyPromotedError` → HTTP 409

**新表**：[src/evalgate/db/migrations/versions/0006_create_eval_case_memberships.py](../src/evalgate/db/migrations/versions/0006_create_eval_case_memberships.py)

**API breaking change（仅 Phase 7 路径，未影响 Phase 1–6）**：

| 接口 | 7 → 7.5 |
|---|---|
| `POST /v1/badcases/{id}/promote` 响应 | `EvalCaseOut` → `PromotionOut` |
| `evalgate badcase promote` 输出 | case 字段集 → membership 字段集 |
| 失败码 | 加 `already_promoted` (409 / CLI rc=1) |

**向后兼容验证**：旧 Phase 1–6 测试零修改全绿；`evalgate run` 在 promote 后的 target set 上能正常迭代到所有 case（含 membership）。

**测试**：原 19 个 Phase 7 测试中 7 个改写（适应 membership 字段名），新增 5 个（`already_promoted` × 3 层 + `list_cases` 看到 promoted case + GET detail 端点同样可见）= 全套 24 个 badcase 相关测试，**总 123 全绿**。

---

## 附录：Phase 4.5 收尾 · 删 `EvalCaseRow.eval_set_id`

Status: **DONE**

Phase 7.5 保留 `EvalCaseRow.eval_set_id` 是「为了向后兼容 Phase 4 / 5 / 6 一行不改」的妥协。这一阶段把它移除：membership 表成为**唯一**的 case→set 归属真理源。

### Δ

- 移除 ORM 字段 `EvalCaseRow.eval_set_id`
- 新 migration [0007](../src/evalgate/db/migrations/versions/0007_drop_eval_case_eval_set_id.py)：
  1. backfill 每行 `EvalCaseRow.eval_set_id` 进 `eval_case_set_memberships`（dedup 已存在 (case, set)）
  2. `batch_alter_table` 删 `ix_eval_cases_eval_set_id` + 列本身
  3. `downgrade()` 可逆：加回列 → 用 oldest membership 的 set 回填 → enforce NOT NULL + FK + index
- `eval_set/repository.add_case[_from_trace]`：同事务内补一条「originating membership」（`promoted_from_result_id=NULL, strategy=NULL, tags=[]`）
- `list_cases(set_id)`：单 JOIN，删 7.5 的 union + dedup
- `badcase/repository.promote_result_to_set`：移除 `SameSetPromotionError`；"promote 进原 set" 结构上就是「写第二次同 (case, set)」，落到 `AlreadyPromotedError` (409)
- API 契约：`EvalCaseOut` 删 `eval_set_id` 字段；container 是 set / payload 是 case，二者通过 `GET /v1/eval-sets/{id}` 的嵌套或 `PromotionOut` 关联

### 关键设计抉择

1. **Migration 先 backfill 再 drop**：反过来 PG 会 FK violation；data migration 写在 Python 里跨方言。
2. **真写 downgrade**：取 case 最早一条 membership.created_at 还原 primary，复原 Phase 4 的原意。
3. **不保留 `SameSetPromotionError`**：仅是 `AlreadyPromotedError` 的别名，Phase 4.5 后两者结构同源；少一类错误码 = semantic 更清。
4. **Tests 改走生产路径**：原直接 seed `EvalCaseRow(eval_set_id=...)` 的 3 个 fixture 全部改 `set_repo.add_case`，顺带覆盖 add_case 的「originating membership」插入。
5. **零运行时性能回退**：原 union + dedup → 单 JOIN，查询计划反而更简洁。
