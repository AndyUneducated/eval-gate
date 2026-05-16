# 贡献指南 / Contributing

欢迎为 EvalGate 提 issue 或 PR —— 尤其欢迎以下方向：

- 新增 judge 任务（RAG / Agent 之外的任务类型）。
- 补充新的卡口维度（quality / cost / latency / safety 之外）。
- 为非 OTel 的 trace 源写 adapter（OpenAI Evals、LangSmith、自家 trace 表 ……）。

## 1. 准备本地环境

```bash
# 装 uv —— https://docs.astral.sh/uv/
uv sync   # 同时安装 runtime + dev 组（pytest、ruff、pre-commit、OTel SDK）

# 起本地 Postgres
make db-up

# 跑 migrations
uv run alembic upgrade head
```

要求：

- Python 3.12+
- Docker（用于本地 Postgres）
- 可选：本地 LLM 推理（Ollama / LiteLLM 配置），用于跑真实 judge 而不是 fixtures

## 2. 提交前自检（与 CI 一致）

```bash
make lint        # ruff check + format 检查
make test        # pytest（async-mode auto）
```

或者把 pre-commit 接进 git：

```bash
pre-commit install
```

## 3. 数据库 schema 改动

走 Alembic：

```bash
# 在干净 DB 上自动生成 migration
uv run alembic revision --autogenerate -m "<msg>"

# 提交 migration 与对应的 model 改动放在同一个 commit 里
```

不要手动改已经合入 main 的 migration 文件 —— 写一个新的覆盖。

## 4. 文档约定

- **关键技术决策**写到 [`DECISIONS.md`](DECISIONS.md)，按 ADR 风格追加。
- **已上线阶段**写到 [`JOURNAL.md`](JOURNAL.md)，每个阶段一段话。
- **产品级改动**（新增维度、改变 API 表面）写到 [`docs/design.md`](docs/design.md)。
- **roadmap 状态**更新 [`docs/ROADMAP.md`](docs/ROADMAP.md) 的 `[DONE]` / `[NEXT]` / `[TODO]`。

## 5. Commit 信息

- 用英文简短说明，遵循 conventional commits（`feat(scope):` / `fix(scope):` / `docs:` / `refactor(scope):` …）。
- `scope` 推荐取自子模块名：`gate` / `judge` / `eval-sets` / `api` / `ui` / `ingest` …。
- 一个 commit 只做一件事；跨模块改动尽量拆开。

## 6. CI 必须通过

每个 PR 都会跑：

- `ci.yml`：lint + tests（不会跑真实 LLM）。
- `eval-gate.yml`：多维度回归卡口；任何一个维度统计显著回归就 fail。

## 7. 较大的提案

新增卡口维度、改 API 兼容性、或调整 trace 表 schema 这类改动，
**先开 issue 讨论方向**，避免实现完才发现要重做。

—— 感谢贡献！
