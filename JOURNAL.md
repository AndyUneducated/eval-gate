# JOURNAL · 里程碑日志

> 每个 phase 完成后，在**顶部**追加一条。一条 ≈ 1 段话，包含：日期、phase 编号、做了什么、用了什么关键技术、有没有 trade-off / surprise。
>
> 不写 "今天我修了一个小 bug"。只写值得未来回顾的事 —— 简历能讲、系统设计上一个新形状、性能/质量 数据出来了 等。
>
> 最新在最上面，最早在最下面。

---

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
