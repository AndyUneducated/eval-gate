# Judge Agreement + Significance/Calibration 复盘 · Cohen's κ · guarded p95 · conditional calibration

## 一句话

Phase 17 收口三件互相关联的统计尾巴：(1) 用 **Cohen's κ（Cohen 1960，把"判得一致"扣掉"碰巧一致"）** 量化 judge 二元判定与人工标签的一致性（对齐设计文档 ~0.85 的目标）；(2) 把 gate 的 **p95 尾延迟显著性**从"重采样解释微妙的裸 bootstrap"升级成**平滑 + 样本量守卫**的 bootstrap（还清 ADR-004 留的技术债）；(3) 把 Phase 16 的**单一全局温度**扩展成**按 `task_type` / `judge_model` 的条件校准曲线**（读时按分组选 T，落实 ADR-013 预留、未实现的扩展位）。三者共用既有 `human_labels` 表与既有 bootstrap/温标引擎，**零新 migration**。

## 数据流

```mermaid
flowchart TD
  subgraph shared["共用底座（无新表）"]
    HL[("human_labels 表<br/>Phase 16 建")] --> Pairs
    Run["eval_results(score, eval_case_id, eval_run_id)"] --> Pairs["fetch_scored_labels<br/>→ (scores, labels, ids)"]
    Pairs --> GK["fetch_group_keys(scope)<br/>join eval_cases.task_type /<br/>eval_runs.judge_model"]
  end

  Pairs --> Kap["evaluate_agreement<br/>(binarize@0.5 → 2x2 → κ + bootstrap CI)"]
  GK --> Kap
  Kap --> KRep["evalgate calibration kappa<br/>AgreementReport (+per-group κ)"]

  Pairs --> Cal["_fit_calibrator(scope)<br/>global T + per-group T"]
  GK --> Cal
  Cal --> Params["calibration_params.json<br/>{temperature, scope, groups:{...}}"]
  Params --> RT["read-time Calibrator<br/>transform(score, group)"]
  RT --> Bad["badcase find_uncertainty<br/>按分组曲线的校准不确定度排序"]

  Base["baseline vs candidate<br/>latency_ms"] --> P95["bootstrap_diff_ci(statistic=p95,<br/>smooth=True, min_reliable_n=20)"]
  P95 --> Gate["latency_p95 轴<br/>reliable 才可能 significant"]
```

## 统计设计（核心）

### ① Cohen's κ：judge vs 人工一致性

把 judge 的 `score` 在**决策阈值** `threshold`（默认 0.5）上二值化成 good/bad 判定，与人工 good/bad 标签配成 2×2 混淆表 `(tp, fp, fn, tn)`：

- **观测一致率** `p_o = (tp + tn) / n`。
- **期望（碰巧）一致率** `p_e = j₊·h₊ + (1−j₊)(1−h₊)`，其中 `j₊ / h₊` 是 judge / 人工各自的"判 good 率"（边际）。
- **κ = (p_o − p_e) / (1 − p_e)**：1=完美、0=只到随机水平、<0=比随机还差。退化守卫：`p_e ≥ 1`（两个 rater 全判同一类）时 κ 无定义，按惯例完全一致返 1、否则返 0。
- **bootstrap CI**：对 `(judge, human)` 配对重采样 1000 次算 κ 分布取 95% 分位——与 gate 显著性同一套重采样思想，判断"κ 是否稳稳高于随机"。

> 为什么κ 而不是准确率：准确率在类别失衡时会被多数类灌水（judge/人工都倾向说 good 时，"蒙"也能到 80% 准确率）；κ 扣掉这份"碰巧"，才是**judge 能不能替代人工**的诚实度量。这也正是设计文档 talking point 里 "κ vs 人工 ~0.85、逼近 double-human 上限" 的那把尺。

### ② p95 显著性复盘：平滑 + 样本量守卫（还 ADR-004 的债）

ADR-004 v1 里 p95 轴"先用阈值"，注记"重采样的 p95 解释比较微妙，留待 Phase 17 复盘"。微妙在哪：**高分位的裸非参 bootstrap 只会来回洗同样那 1–2 个尾部次序统计量**，CI 因此离散/lumpy、覆盖率偏低，小样本下尤甚。两个标准修法，都上：

- **平滑 bootstrap（`smooth=True`）**：每次重采样叠一层核噪声 `N(0, h²)`，带宽用 Silverman 经验法则 `h = 0.9·σ·n^(−1/5)`。把离散经验 CDF 抹成连续的，给尾分位一个稳定、覆盖更好的 CI。
- **可靠性守卫（`min_reliable_n`）**：样本量低于阈值（gate 用 **20**，约当 1 个观测越过 95 分位的最小尾部支撑）时，结果标 `reliable=False` 且**强制 `significant=False`**——一条尾部数据太薄的轴**永远不会 false-block 一个 PR**（正是 ADR-004 存在的意义）。

保留 10% 相对容差带作 belt-and-suspenders；但显著性判定现在是"平滑 bootstrap CI 不跨 0 **且** 可靠"，不再是裸阈值。

### ③ 条件校准：per-`task_type` / per-`judge_model` 温度

judge 常常在一类任务上过自信、在另一类上却校准良好（跨 judge 模型同理），单一全局 T 会在分组间留下 ECE。Phase 17 把 `Calibrator` 泛化成**带分组的温度族**：

- 拟合时：先在**全体**拟合一个全局 `temperature`（读时兜底），再对每个**数据充足**（沿用 `n ≥ 10` + 双类的同一门槛）的分组各拟合一条 T；数据太薄的分组不给独立曲线、读时回落到全局 T。
- 读时：`Calibrator.transform(score, group)` 按 `group` 选 T，未见/薄分组回落全局——`scope="global"` 时行为与 Phase 16 逐字节一致（严格向后兼容）。
- badcase：`find_uncertainty` 在 calibrator 为非 global scope 时，join 出每行的分组键，用**对应曲线**算校准后不确定度排序（reason 追加 `[task_type=rag]` 之类）。

`calibration_params.json` 新形状（`groups` 为空即等价旧的全局文件）：

```json
{ "temperature": 3.14, "scope": "task_type",
  "groups": { "rag":   {"temperature": 2.41, "n": 400, "ece_before": 0.16, "ece_after": 0.03},
              "agent": {"temperature": 3.78, "n": 400, "ece_before": 0.19, "ece_after": 0.03} },
  "n": 800, "ece_before": 0.17, "ece_after": 0.03, "fitted_at": "..." }
```

## 技术选型与抉择

> 见 [DECISIONS.md](../DECISIONS.md) ADR-014 / 015 / 016。下面是面试视角的"岔路 → 选择 → 代价"。

| 岔路 | 选择 | 备选 | 为什么 / 代价 |
| --- | --- | --- | --- |
| 一致性度量 | **Cohen's κ** | raw accuracy / F1 | κ 扣掉"碰巧一致"，类别失衡下才诚实；代价是需要"judge 二元判定"这一步（阈值化 `score`）。 |
| κ 的标签源 | **复用 `human_labels` 表** | 新建表 | ADR-013 早就把这张表设计成"一表喂两 phase"；κ 直接 `fetch_scored_labels` 拿 `(score, label)`，零新 migration、零新存储。 |
| judge 判定 | **`score ≥ threshold`（默认 0.5，可调）** | 学一个判定阈值 | gate 的通过语义本就是"分数过线"，用同一根线最自洽；阈值做成 `--threshold` 以适配不同任务的"好"标准。 |
| p95 显著性 | **平滑 + 样本守卫的 bootstrap** | 纯阈值 / 纯裸 bootstrap / studentized bootstrap | 平滑修尾分位离散、守卫防小样本 false-block；比 studentized 简单、无需估计方差的方差。代价：平滑带宽是个近似、并让 CI 略偏宽（偏保守，但保守正是 gate 想要的）。 |
| 校准分组粒度 | **`global` / `task_type` / `judge_model` 三档** | 每 (task×judge) 笛卡尔积 | task_type / judge_model 是异质性的两大来源，也是数据能撑住的粒度；笛卡尔积会把每格标注量摊薄到拟合不动。薄分组一律回落全局 T。 |
| 分组信息何时取 | **读时 join（task_type ← eval_cases，judge_model ← eval_runs）** | 拟合时把分组写进结果行 | 延续"存原始、读时变换"：`eval_results` 不加列，曲线随时可换 scope 重拟合。代价：badcase 读时多一两条 `IN (...)` 查询。 |

**已知代价**：κ 依赖决策阈值的选取（默认 0.5，与 gate 语义一致但非普适最优）；平滑 bootstrap 的带宽是经验法则、CI 偏保守；条件校准的分组门槛沿用 `n ≥ 10`，标注稀疏时多数分组仍回落全局 T（此时等价 Phase 16）。

## 模块布局（沿用 `report/` = 纯统计、子包 = 编排）

- [src/evalgate/report/agreement.py](../src/evalgate/report/agreement.py) —— **新增**纯引擎：`binarize_scores`、`confusion_counts`/`Confusion`、`cohen_kappa`、`evaluate_agreement`（κ + 混淆 + 边际 + bootstrap CI），只依赖 numpy、无 DB/LLM，与 `calibration.py` 同构。
- [src/evalgate/report/significance.py](../src/evalgate/report/significance.py) —— `bootstrap_diff_ci` 加 `smooth` / `min_reliable_n`；`BootstrapResult` 加 `reliable` / `n_effective`；`_silverman_bandwidth` / `_resample` 平滑核。mean 轴默认行为不变。
- [src/evalgate/report/calibration.py](../src/evalgate/report/calibration.py) —— `Calibrator` 加 `scope` / `group_temperatures` + `temperature_for` / `transform(…, group)` / `transform_array(…, groups)`；`from_dict` 兼容旧文件与新 `groups` 形状；`evaluate_calibration(…, groups=)`。
- [src/evalgate/report/multi_axis.py](../src/evalgate/report/multi_axis.py) —— `latency_p95` 轴改走 `smooth=True, min_reliable_n=P95_MIN_RELIABLE_N(=20)`；mean 轴不变。
- [src/evalgate/calibration/repository.py](../src/evalgate/calibration/repository.py) —— `group_keys_for_rows` / `fetch_group_keys`（join 出分组键）、`_fit_calibrator`（全局 + 分组拟合）、`fit_and_save(scope=)` / `compute_report(scope=)`、**新增** `compute_agreement(run_id, threshold, scope)`。
- [src/evalgate/badcase/finder.py](../src/evalgate/badcase/finder.py) —— `find_uncertainty` 在非 global scope 时按分组曲线排序。

## Schema

- [src/evalgate/core/schemas.py](../src/evalgate/core/schemas.py)：`CalibrationGroup`（per-group 温度 + ECE）+ `CalibrationReport` 加 `scope` / `groups`；**新增** `AgreementGroup` + `AgreementReport`（κ、观测/期望一致率、CI、双方 positive rate、混淆四格、`scope` / `groups`）。

## CLI

沿用 `_add_calibration_subcommands`（一表喂两 phase → 同一命令组）：

```bash
# κ：judge 判定 vs 人工标签一致性（可按分组 + 调阈值）
evalgate calibration kappa [--run <id>] [--threshold 0.5] [--scope task_type|judge_model]
#   打印 {n, threshold, cohen_kappa, ci_low/high, observed/expected_agreement, tp/fp/fn/tn, groups}

# 条件校准：按分组拟合多条曲线
evalgate calibration fit --scope task_type [--out calibration_params.json]
#   打印 {scope, temperature(全局兜底), groups:{rag:{temperature,n,ece_after}, ...}}

# report / badcase 会自动识别 params 文件里的 scope，读时按分组选 T
evalgate calibration report --params calibration_params.json
evalgate badcase list --strategy uncertainty --calibration calibration_params.json
```

退出码沿用约定：`0` ok / `1` 预期性缺失 / `2` 错误（如无标注可算 κ / 拟合）。

## 验证策略

- **κ 引擎**（[test_agreement_stats.py](../tests/test_agreement_stats.py)）：完美一致→κ=1；随机水平→κ≈0；退化单类→按惯例 1/0；bootstrap CI 括住点估计且强一致时 `ci_low>0`。
- **κ 编排/CLI**（[test_agreement_repository.py](../tests/test_agreement_repository.py)）：`compute_agreement` 全局 + per-`task_type`；无标注抛 `InsufficientLabelsError`；`calibration kappa` CLI 端到端。
- **p95 守卫**（[test_significance_bootstrap.py](../tests/test_significance_bootstrap.py)）：足量数据的真尾部回归 `significant & reliable`；8 样本尾部 `reliable=False & significant=False`（不 false-block）；mean 轴默认仍 `reliable`。
- **条件校准**（[test_calibration_stats.py](../tests/test_calibration_stats.py) / [test_calibration_repository.py](../tests/test_calibration_repository.py) / [test_calibration_cli.py](../tests/test_calibration_cli.py) / [test_badcase_calibrated.py](../tests/test_badcase_calibrated.py)）：分组选 T + 回落全局、字典往返、per-`task_type` 拟合出多曲线、badcase 按分组曲线排序。
- **离线 smoke**：`make kappa-smoke`（[scripts/phase17_kappa_smoke.py](../scripts/phase17_kappa_smoke.py)）在 seeded 合成数据上一次跑通三件事——κ≈0.82 且 CI 高于 0、真 p95 回归显著而薄样本不显著、条件校准 ECE ≤ 全局 ECE。

> 离线说明：与 Phase 15/16 同款诚实取舍——mock judge 恒返 0.5（零信息、零方差），κ / 显著性 / 校准 demo 在它上面都跑不起来，所以 smoke 直接驱动纯引擎、喂 seeded 数据，这正是真实标注集/真实延迟样本的形状。
