# EvalGate

> **以 Eval 为先的 LLMOps + CI 卡口** —— 把线上 LLM trace 转化为多维度回归门，
> 让有问题的 PR 在合入前就被拦下来。

[![CI](https://github.com/AndyUneducated/eval-gate/actions/workflows/ci.yml/badge.svg)](https://github.com/AndyUneducated/eval-gate/actions/workflows/ci.yml)
[![eval-gate](https://github.com/AndyUneducated/eval-gate/actions/workflows/eval-gate.yml/badge.svg)](https://github.com/AndyUneducated/eval-gate/actions/workflows/eval-gate.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![codecov](https://img.shields.io/badge/coverage-pending-lightgrey.svg)](https://codecov.io)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![repo size](https://img.shields.io/github/repo-size/AndyUneducated/eval-gate)](https://github.com/AndyUneducated/eval-gate)

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/badge/uv-managed-261230.svg?logo=astral&logoColor=white)](https://docs.astral.sh/uv/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Pydantic v2](https://img.shields.io/badge/Pydantic-v2-E92063.svg?logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-D71F00.svg?logo=sqlalchemy&logoColor=white)](https://www.sqlalchemy.org/)
[![Alembic](https://img.shields.io/badge/Alembic-migrations-6BA539.svg)](https://alembic.sqlalchemy.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1.svg?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-425CC7.svg?logo=opentelemetry&logoColor=white)](https://opentelemetry.io/)
[![LiteLLM](https://img.shields.io/badge/LiteLLM-multi--provider-8A2BE2.svg)](https://github.com/BerriAI/litellm)
[![Ragas](https://img.shields.io/badge/Ragas-judges-7B61FF.svg)](https://docs.ragas.io/)
[![Presidio](https://img.shields.io/badge/Presidio-PII-1E90FF.svg?logo=microsoft&logoColor=white)](https://microsoft.github.io/presidio/)
[![Streamlit](https://img.shields.io/badge/Streamlit-ops_UI-FF4B4B.svg?logo=streamlit&logoColor=white)](https://streamlit.io/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![pytest](https://img.shields.io/badge/tests-pytest-0A9EDC.svg?logo=pytest&logoColor=white)](https://docs.pytest.org/)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen.svg?logo=pre-commit)](https://pre-commit.com/)

---

## 为什么做 EvalGate

LLM 类 PR 上墙时通常只挂一个数字 —— *"pass rate 降了 0.5%，应该没事"* —— 而这个数字同时在四个维度上是错的。一个真正能用的 CI 卡口必须同时拒绝 **质量、成本、延迟、安全** 四类回归，并且要有足够的统计严谨度撑住随机性 judge，还要把锅指到具体的 intent / tag 上。

| PR 作者关心什么 | 真正要算清这个问题，你需要什么 | 朴素的 eval pass-rate 卡口为什么不够 |
|---|---|---|
| *"回答质量退步了吗？"* | 按任务跑 bootstrap-CI 的 pass rate | 随机性 LLM judge 在相同输入下也会漂 1–3 个点；朴素差值要么把噪声当回归，要么漏掉真的回归。 |
| *"这个 PR 是不是更贵了？"* | 按 tag / intent 切的 token 消耗变化 | 平均 *"+5% tokens"* 会盖住 *"billing intent +50%、其它打平"* —— 而后者恰恰才是你想抓的回归。 |
| *"用户会不会变卡？"* | 看 p95 延迟，不是均值 | p50 可以稳如老狗，长尾却已经炸了，用户感受到的是长尾。 |
| *"是不是开了新的安全口子？"* | 拆成 4 个子维度：PII 入 / PII 漏出、jailbreak 尝试 / jailbreak 顺从 | 单一的 *"违规率"* 把 *"有人试图越狱"*（输入）和 *"模型真的照办了"*（输出）混在一起 —— 这两个信号方向相反，修复手段也完全不同。 |
| *"这次回归是真的还是噪声？"* | 每个维度都要 bootstrap CI + 显著性标签 | 没有显著性，每个 PR 要么靠运气绿、要么靠运气红，一周之内卡口就会被人关掉。 |
| *"是在哪退步的？"* | 每份报告都附 tag / intent 归因表 | 聚合数字没法对应到具体负责人；按 tag 切的明细行可以。 |

EvalGate 把每个维度路由到合适的统计方法，并把结果汇总到同一条 PR 评论里，让卡口的判断是事实，而不是一句口头评价。

## 它到底做什么

EvalGate 摄取你的 LLM 应用发出的 OpenTelemetry trace，通过不确定性采样挖出 **BadCase**，
在每个 PR 上跑一套 **任务感知 judge**（RAG / Agent / 通用），当四维卡口触发时直接 **block 合入**：

- **quality** —— pass rate，用 bootstrap-CI 显著性顶住随机 eval 噪声
- **cost** —— token 消耗回归
- **latency** —— p95 延迟回归
- **safety** —— PII（Presidio）与 jailbreak（关键词 + LLM 分类器）违规率，拆成四个子维度（`pii_input_rate` / `pii_output_leak_rate` / `jailbreak_attempt_rate` / `jailbreak_compliance_rate`）

回归会按 `tag` / `intent` 做归因，因此报告写的是
*"billing intent 掉了 8 个点"*，而不是 *"pass rate 掉了 0.5%"*。

> **状态**：多维度 CI 卡口 v1 已落地（基于 fixtures 驱动）。下一步是接真实 OTel ingest 与 judge runner。

## 项目文档

| 文件 | 写了什么 |
|---|---|
| [`docs/design.md`](docs/design.md) | 完整的产品 + 技术 spec —— 功能、架构、取舍的唯一信息源，先看这个。 |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | 分阶段的交付计划（每个阶段大约 1 人天），用 `[DONE]` / `[NEXT]` / `[TODO]` 跟踪。 |
| [`DECISIONS.md`](DECISIONS.md) | ADR 风格的关键技术决策日志（为什么用 OTel、为什么 PG+JSONB、为什么砍掉 prompt UI ……）。 |
| [`JOURNAL.md`](JOURNAL.md) | 倒序的里程碑日志 —— 每个上线阶段一段话。 |

## 快速开始

```bash
# 1. 装 uv（https://docs.astral.sh/uv/），然后：
uv sync

# 2. 起 Postgres
make db-up

# 3. 跑测试
make test

# 4. 用 demo fixtures 试一下多维度卡口
uv run python scripts/seed_demo.py
uv run evalgate gate \
  --baseline examples/fixtures/baseline.json \
  --candidate examples/fixtures/candidate.json
# exit 0 = 卡口通过，exit 1 = 检测到回归（CI 会用这个返回码）
```

## CI 卡口

`eval-gate` workflow 会在每个 PR 上跑：先 seed demo eval 记录，调用 `evalgate gate`，把 JSON 报告作为
artifact 上传，在 PR 上发四维度的归因表评论，任何一个维度出现"统计显著的回归"就让这次 check 失败。
把 seed 的 fixtures 换成你自己 baseline / candidate eval 输出，就能把卡口接到你自己的 pipeline 上。

## 开发

| 命令 | 作用 |
|---|---|
| `make install` | 把所有依赖（含 dev 工具）装到 `.venv/` |
| `make dev` | 用 Docker 启动本地 Postgres |
| `make test` | 跑 pytest |
| `make lint` | Ruff check + format 检查 |
| `make format` | 自动修复 lint + format |
| `make db-up` / `make db-down` | 管理本地 Postgres |
| `make ui` | 在 `http://127.0.0.1:8501` 启动 Streamlit 运维 UI（通过 HTTP 调 `evalgate-api`） |

## 运维 UI（Phase 11）

`src/evalgate/ui/` 下是只读的 Streamlit UI。它只走 FastAPI 后端的 `/v1/*`（绝不直接连 DB），
所以它跟 CLI / CI 用的是同一套 REST 表面，是这套 API 的真实消费方。

```bash
make db-up                      # 起 Postgres
uv run alembic upgrade head     # 跑 migrations
uv run python scripts/seed_demo.py
uv run evalgate-api             # 一个 shell — 8000 端口
make ui                         # 另一个 shell — 8501 端口，会自动开浏览器
```

三个页面：

1. **Traces** —— 分页列表 + span 树详情；"Promote to eval set" 按钮包了
   `POST /v1/eval-sets/{id}/cases/from-trace/{trace_id}`。
2. **Eval Sets** —— 新建 eval set；选中后可以看它的 cases。
3. **Reports** —— 选一个 eval set、两个 `eval_runs`（baseline / candidate），
   渲染四维卡口结论 + 子维度（RAG / safety）+ tag 归因。

API 地址用 `EVALGATE_API_URL` 配置（默认 `http://127.0.0.1:8000`）。

## 贡献

欢迎 PR —— 详细流程见 [CONTRIBUTING.md](./CONTRIBUTING.md)。
特别欢迎：新增 judge 任务、补充新的卡口维度、为非 OTel 的 trace 源写 adapter。

## License

Apache-2.0，详见 [LICENSE](LICENSE)。
