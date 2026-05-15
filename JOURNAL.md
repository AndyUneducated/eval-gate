# JOURNAL · 里程碑日志

> 每个 phase 完成后，在**顶部**追加一条。一条 ≈ 1 段话，包含：日期、phase 编号、做了什么、用了什么关键技术、有没有 trade-off / surprise。
>
> 不写 "今天我修了一个小 bug"。只写值得未来回顾的事 —— 简历能讲、系统设计上一个新形状、性能/质量 数据出来了 等。
>
> 最新在最上面，最早在最下面。

---

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
