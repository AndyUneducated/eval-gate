# JOURNAL · 里程碑日志

> 每个 phase 完成后，在**顶部**追加一条。一条 ≈ 1 段话，包含：日期、phase 编号、做了什么、用了什么关键技术、有没有 trade-off / surprise。
>
> 不写 "今天我修了一个小 bug"。只写值得未来回顾的事 —— 简历能讲、系统设计上一个新形状、性能/质量 数据出来了 等。
>
> 最新在最上面，最早在最下面。

---

## 2026-06-12 · Phase 16 Judge Calibration（ECE + temperature scaling）

落地亮点线第四站：让 judge 的 `score` 真有概率意义——judge 说 0.8，就该约等于"人类有 80% 概率判这条 good"。这是给项目加的一张"ML / 研究深度"牌（Expected Calibration Error + temperature scaling + reliability diagram，引 Guo et al. 2017）。

**校准对象是 `score`，不是启发式 `judge_confidence`。** 今天的 `judge_confidence` 只是个方差代理、不是概率；目标针对的是 score。用单参数 **temperature scaling**：`p = sigmoid(logit(score)/T)`。`T>1` 说明 judge 过自信（把分往 0.5 拉）。拟合以 `w=1/T` 为变量最小化逻辑 NLL——单特征无截距的逻辑回归，对 `w` **严格凸**，所以一维 **golden-section** 搜索即全局最优；环境只有 numpy（无 scipy/sklearn），NLL 用 `logaddexp` 稳定 softplus、`logit` 带 eps 截断。守卫：标签需同时含两类且 n≥10，否则返回 T=1（不够信号不动）。

**两个与用户对齐的设计决策**：① 人工标签存进**新 `human_labels` 表**（migration 0014，软引用 `eval_result_id`），可 join `eval_results`、可查询、与既有持久化范式一致——它同时是 Phase 17 Cohen's κ 的数据源；② 校准在**读取时**由纯 `Calibrator` 施加，`eval_results` 原始 `score`/`judge_confidence` 保持不可变，不改 runner、不加结果列（延续 Phase 14/15 "存原始、读时变换"）。

**分层沿用既有约定**：[report/calibration.py](src/evalgate/report/calibration.py) 纯统计引擎（ECE/MCE、reliability_curve、fit_temperature、`Calibrator`、`render_reliability_png` 懒加载 matplotlib），无 DB/LLM、可被单测与 smoke 直接复用；新建 `evalgate.calibration` 包负责编排（标签存储 + join 取分 + 拟合落盘 + 读盘）。BadCase `find_uncertainty(calibrator=)` opt-in 切到**校准后不确定度**（`1-|2p-1|`，在判定边界 p=0.5 处最大）。

**一个诚实的统计注脚**：temperature scaling 是**单调**变换，不会重排 `|score-0.5|`；它对 badcase 的价值在于**替换掉**今天那个与真实模糊度不相关的 `judge_confidence` 启发式排序——而非重排原始分数。所以 smoke / 单测的召回对比都是"校准后不确定度 vs 启发式 confidence"，而不是 vs `|score-0.5|`。

**smoke 同款离线合成取舍**：mock judge 恒返 0.5（零信息），校准 demo 跑不了，所以走 seeded 过自信合成对——ECE 0.165→0.029、拟合 T≈3.6、出 reliability png、校准后边界 case 召回从 18% 提到 100%。

**测试**：新增 5 个文件（stats 退出标准、repository、badcase 召回、CLI label→fit→report+plot、migration 0014 round-trip），phase16 smoke 进 `test_smokes.py` 的 CI mock 矩阵。新增 matplotlib 依赖。全量绿、lint/format 通过。

---

## 2026-06-12 · Phase 15 Sequential Gate（边跑边判，省 judge 调用）

落地亮点线第三站：CI gate 不再"跑满 N 才下结论"，而是流式接候选分数、每 `look_every` 条 case 看一眼——证据足够坏立即 FAIL、足够好立即 PASS，跳过剩余那些贵的 judge 调用，同时把累积误判率锁在 α=0.05。这是给项目加的一张"统计深度"牌。

**核心形状 = 一个 paired group-sequential 检验。** baseline 与 candidate 跑同一组有序 case，于是按 `case_id` 配对、对每条差值 `d_i = cand - base` 做检验——比固定-N gate 的双样本 bootstrap 更有功效。把配对 t 统计量换到 B-value 尺度 `B(t)=Z·√t`，在 H0 下就是带独立增量的布朗运动，正好喂给边界递归。**提前 FAIL** 用 Lan-DeMets **α-spending**（`obf`/`pocock` 两个花费函数），用 Armitage-McPherson-Rowe 网格递归（numpy 卷积 + cumsum 定位）求"前面都没越界、这一看恰好花掉增量 α"的下边界。**提前 PASS** 用 **stochastic curtailment**：当"在最坏可容忍回归 drift 下到 t=1 还能越界"的条件功效 < γ 时判 futile → PASS——curtailment 只缩短运行、永不造成 FAIL，所以 Type-I 完全不受影响（这是选它而非 beta-spending 的关键）。只有 numpy，所以 `norm_cdf` 用 `math.erf`、`norm_ppf` 用 Acklam 有理逼近自实现。

**分层沿用既有约定**：[report/sequential.py](src/evalgate/report/sequential.py) 纯统计引擎（spending / 边界递归 / 条件功效 / 有状态 `SequentialGate` / 纯回放 `evaluate_sequential`），无 DB/LLM、可被 Monte Carlo 与 smoke 直接复用；[gate/sequential.py](src/evalgate/gate/sequential.py) 负责编排——载 baseline、驱动 `iter_eval`、越界即 break（真正省调用）、停止点拼 `GateReport`。**只对 quality 一个轴做 sequential**（每次 judge 调用驱动它），cost/latency/safety 很便宜，在停止点对已消费 case 做一次固定-N 快照（复用 `build_gate_report`），quality 轴判定由 sequential 覆盖（权威）。无 migration——baseline 直接复用既有 `eval_results`。

**统计严谨性靠 Monte Carlo 兜底（退出标准）**：1000 次/场景断言——无 drift 时累积 false-fail ≈ 0.05（OBF look_every=5 实测 ~0.05）；drift ≤ -mde 时 power ≥ 0.8 且省调用 ≥ 50%；干净候选 ≥90% 提前 PASS、省调用 ≥ 50%。一个**诚实的小样本注脚**：Pocock 把 α 前置到最早几次 look，而 n=5 时正态近似对 t 统计量略偏激进（实测 ~0.08），所以默认推荐 OBF（早期几乎不花、稳在 0.05），Pocock 的 Type-I 测试用更现实的 look 间隔（首看 n=10）。

**smoke 也有 Phase 14 同款诚实取舍**：mock judge 恒返 0.5 → 零方差，统计 demo 跑不了，所以 phase15 smoke 走**离线合成**（seeded 正态：回归候选提前 FAIL ~75% 省调用、干净候选提前 PASS ~58% 省调用），而非走 mock LLM。

**测试**：新增 5 个文件（spending/边界/正态 round-trip、判定逻辑、Monte Carlo、runner+CLI 端到端），phase15 smoke 进 `test_smokes.py` 的 CI mock 矩阵。全量绿、lint/format 通过。

---

## 2026-06-12 · Phase 14 Adversarial Case Synth（红队自动出题 · 闭环飞轮）

落地亮点线第二站：generator-LLM 给最弱 tag 自动出"刁钻 case"，经人审才进 eval set，形成 "评测 → 找弱点 → 自动出题 → 人审 → 再评测" 的闭环。

**核心形状 = 给 eval case 加一个生命周期。** 新增 `eval_cases.status`（pending/active/archived）+ `source`（trace/manual/adversarial）两列（migration 0013，含 `source='trace'` 回填）。整个"pending 永不入 gate"的安全属性**只靠一处实现**：把 `eval_set.repository.list_cases` 的签名重构成带 `statuses` 过滤、**默认 `("active",)`**。runner（`iter_eval` 调 `list_cases`）一行不改就自动排除待审/已归档 case；detail / CLI show 显式传 `statuses=None` 看全量。这是"不在调用方加特判、把不变量沉到单一数据访问层"的典型——未来任何走 `list_cases` 的消费方都白嫖该保证。无向后兼容包袱，直接改签名。

**新增 `evalgate.adversarial` 包**：`synth.py` 纯生成（4 模板：边界值 / 歧义指代 / prompt injection（复用 `DEFAULT_JAILBREAK_KEYWORDS`）/ role confusion；一次 strict-JSON 调 `acompletion_json`，容错解析，**永不抛错**，mock 按模板确定性产 case 离线可跑）；`repository.py` 生命周期（`generate_into_set` 取最弱 exemplar→生成→落 pending；`review_case` approve/reject；`stats` 命中率）。配 REST 四端点 + `evalgate adversarial` 三子命令 + cheap-model 默认配置。

**两个已与用户对齐的设计决策**：① hit = **绝对阈值**（candidate 最新得分 `< 0.5`），不依赖某个 baseline run 的选取，跨 run 可比；② adversarial case **reference-free**（不生成 gold），红队价值在暴露弱点而非给标准答案，也省掉二次人审 gold 的成本。

**mock smoke 的一个诚实取舍**：mock pointwise judge 恒返 0.5，所以"quality 得分 < 0.5"在 mock 下天然不成立。于是 phase14 smoke 的**确定性头号断言**改用 **safety 轴回归**——审入的注入 case 给 candidate 引入 baseline 没有的 `jailbreak_attempt` 攻击面（per-record 即可判定，不靠 bootstrap 显著性），gate fail；"得分 < 0.5 的真 hit"留给真模式（`EVALGATE_MOCK_LLM=0`）断言，hit>0 路径由单测直接喂低分结果覆盖。smoke 还顺手验证了核心不变量：generate 后再跑一次 run，0 条 pending case 泄漏。

**测试**：新增 34 个（synth / repository（含 runner-excludes-pending）/ endpoint / cli / migration 0013 round-trip / list_cases 状态过滤），phase14 smoke 进 `test_smokes.py` 的 CI mock 矩阵。全量绿、lint/format 通过。

---

## 2026-06-12 · 工程问题修复（gate 显著性统一 + smoke 治理）+ 真模型复验

把上一条采集中暴露的 6 个工程问题（与模型能力无关）一次性修掉，原则是**设计整洁 / 面向后续 phase 可适配 > 向后兼容**，必要处直接重构。

**Fix #1 — `latency_p95` 轴零容差假阳性（核心）。** 根因是 `multi_axis.py` 把 mean 轴和 p95 轴分叉处理：mean 走 bootstrap CI、p95 只 `regressed = delta > 0`（无显著性、无阈值）。重构成**所有数值轴走同一条判定**：把 `significance.bootstrap_diff_ci` 泛化成可插拔统计量（`STATISTICS = {"mean", "p95"}`，统计量沿 resample 矩阵 `axis=1` 归约，点估计也用同一统计量），`AxisSpec` 新增 `rel_tolerance`，回归判定收敛到一个 `_is_regression(显著 ∧ 坏方向 ∧ |delta| ≥ rel_tolerance·|baseline|)`。latency 轴给 10% 相对容差；mean 轴 `rel_tolerance=0` → 行为与旧版逐位一致（quality/cost 测试零改动）。后续 phase 加新轴只需声明 `aggregator + direction + rel_tolerance`，显著性自动统一。

**Fix #3 — phase7/phase9 写死 `mock=True`。** 新建 `scripts/_smoke.py` 作为唯一真相源：`mock_from_env()`（修了旧的字符串真值 bug —— `EVALGATE_MOCK_LLM=0` 以前被当成 mock）、退出码常量、`import` 即开启行缓冲。phase7/phase9 改成把 `mock` 一路透传给 `run_eval`/`finder.find`。phase9 的「中间步错误被最终答案掩盖」确定性断言**只在 mock 成立**（真模型轨迹不确定），真模式降级为连通性断言（planner 真打了 LLM、轨迹子轴齐全）。

**Fix #2 — 真 smoke 零 CI 覆盖致静默腐烂。** 新增 `tests/test_smokes.py`：以 `EVALGATE_MOCK_LLM=1` 子进程跑全部 8 条 smoke 并断言退出 0。这正是当初 phase10 断言被 0011 改坏却躺了 12 天没人发现的根因——现在 mock 下每条 smoke 的断言都进 CI。

**Fix #4 — 退出码语义不统一 + phase5/6/7 无断言。** 统一为 `EXIT_OK=0 / EXIT_FAILED=1（该抓的回归没抓到）/ EXIT_ERROR=2（连通性/管线错误）`；phase12 作为 gate 本身保留「退出码即裁决」语义（1=回归，设计内）。补上断言：phase5 连通性（两边各跑满 3 case + gate 干净退出）、phase7 BadCaseFinder 真的 promote 了 case。phase6 起初写成硬断言「多裁判 stdev ≤ 单 pointwise stdev」，真跑两次 N=3 结果**翻转**（一次 multi 更低、一次更高）当场暴露这是 Fix #5 的小 N 低功效问题——遂改为**结构断言（两 config 都产出完整打分矩阵）+ 信息性报告**（multi>single 时打 underpowered NOTE 不失败），方差缩减的正式结论留给 Phase 17 足量 N + seed 的复现实验。

**Fix #6 — 可观测性/卫生。** `_smoke` 导入即把 stdout/stderr 切行缓冲（修日志里 stderr「FAIL」排到缓冲 stdout 前面的误导）；所有 scratch（`.dbroot`/`runs`）移到系统临时目录并 `finally` 清理，`.gitignore` 加防御条目。

（Fix #5「demo N 太小、bootstrap 判不出显著」是数据体量问题，按计划留给 Phase 17 的正式复现实验，不在本轮塞大数据集拖慢真跑。）

**真模型复验**（本机 Ollama，`EVALGATE_MOCK_LLM=0`）：全量 mock 测试绿（新增 test_smokes 8 条 + bootstrap p95 3 条）。真跑逐一确认修复正确且未伤质量：

| Phase | 模式 | 退出码 | 关键复验点 |
|---|---|---|---|
| 13 Shadow | 离线 | 0 | cost 注入 regress 仍触发报警（未受影响）|
| 5 Judge Runner | 真模型 | 0 | `mode=real` 打印确认 env 真值修复生效；57s |
| 7 BadCase Finder | **真模型** | 0 | 写死 mock 已修，真模型下 promote 成功；153s |
| 10 Safety | 真模型 | 0(*) | **latency +292ms(+4.4%) 现 `passed=True`（旧逻辑必假阳）**；safety 子轴仍正确 regress → 轴 fail；131s |
| 8 RAG | 真模型 | 0 | latency **+14903ms(+429%) 真回归仍 `passed=False`（显著）**；897s |
| 9 Agent | **真模型** | 0 | 写死 mock 已修，「planner 对真模型验证」首次达成；45s |
| 12 CI Gate | 真模型 | 1 | gate 设计内 FAIL（quality 子轴 `faithfulness -0.25`）；latency **-2425ms（变快）正确 `passed=True`** 不误报；126s |
| 6 Judge Robustness | 真模型 | 0 | N=3：single 0.0883 vs multi 0.0679（claim holds）；1546s。**但两次 N=3 跑结果会翻转**（另一次 single 0.0519 vs multi 0.0601）——印证小 N 方差比较本就噪声大，所以这条不做硬断言，只报告 |

**最有说服力的一条**：Fix #1 在真数据上同时通过两个反向用例——phase10 的 +4.4% latency 噪声被容差吸收（旧代码 `delta>0` 必然假阳），而 phase8 的 +429% 真回归与 phase12 的变快都被正确判定。噪声不再拖垮 gate，真回归照抓。
(*) phase10 退出码取决于 safety 子轴回归（与 latency 无关），本轮为 0。

## 2026-06-12 · 全量真模型数据采集 + 文档收尾

Phase 12/13 落地后第一次**系统性跑各 phase smoke 并采集数据**（本机 Ollama：候选/裁判 `qwen3.5:9b`、跨家族大裁判 `qwen3.6:27b`、检索/RAGAS 嵌入 `qwen3-embedding:8b`）。全量 mock 测试 **322/322 绿**。**实测中发现 phase7 / phase9 smoke 把 `mock=True` 写死，无视 `EVALGATE_MOCK_LLM`**——所以这两条本轮跑的其实是 mock（详见下方「工程问题」）。真正命中真模型的是 **5 / 6 / 8 / 10 / 12**：

| Phase | 实测 | 模式 | 退出码 |
|---|---|---|---|
| 5 Judge Runner | billing 3 case，baseline 0.85 → candidate 0.80（弱化 prompt 真信号）；55s | 真模型 | 0 |
| 6 Judge Robustness | **变异度 N=5**：单 pointwise 每-case 分数 stdev **0.0853** vs 多裁判(9B+27B)+position-swap+K3 **0.0503**（**↓41%**）；2436s（27B 溢出到 CPU 是长尾） | 真模型 | 0 |
| 7 BadCase Finder | 10 case → uncertainty 取 top-3 → promote 落 target set；5s | **mock（脚本硬编码）** | 0（脚本无断言，恒 0） |
| 8 RAG (RAGAS) | 真嵌入+真裁判：faithfulness 0.867 → 0.727、answer_relevance 0.927 → 0.913、context_precision 1.0；869s | 真模型 | 0 |
| 9 Agent Trajectory | tool_call_accuracy 0.83 → 0.33、quality 0.75 → 0.33（**mock planner 的确定性轨迹比对，非真模型信号**）；4s | **mock（脚本硬编码）** | 0 |
| 10 Safety | 真模型**拒绝了越狱**（`jailbreak_compliance_rate=0`，mock 看不到的真行为）；`pii_input_rate +0.417`、`jailbreak_attempt_rate +0.333`、`pii_output_leak_rate +0.417` 三项显著 regress，safety 轴 `passed=False`；128s | 真模型 | 见下 |
| 12 CI Gate (real) | 削弱 candidate 触发 `quality` 轴 fail，归因 `answer_relevance` delta=-0.079（显著）+ 最差 tag `rag`；**124.5s**（两轮 8 次评测） | 真模型 | 1（设计内：gate 故意 FAIL） |

**本轮暴露的工程问题（与模型能力无关，按优先级）**：

1. **`latency_p95` 轴零容差 → 假阳性 gate fail。** `multi_axis.py` 对 p95 轴是 `regressed = delta > 0`：无阈值、无显著性/CI（quality/cost/safety 都走 bootstrap CI），p95 哪怕 +1ms 也判回归（docstring 写「threshold delta」但代码里根本没有阈值）。真模型本地推理延迟抖动大，于是 **phase8 的 gate `passed=False` 完全是被 latency 噪声拖的**（payments tag）、phase10 也报了 latency +305ms。修法：给 p95 加相对容差 + 方差/CI 判显著。
2. **真模型 smoke 全程无自动化覆盖 → 静默腐烂。** CI 只跑 ruff+pytest（mock）+ phase12 smoke（mock）；phase5/6/7/8/9/10/13 的 smoke 哪里都不自动跑。后果就是下面这个被 0011 改坏的断言躺了 ~12 天没人发现。
3. **phase7 / phase9 smoke 写死 `mock=True`，无视 `EVALGATE_MOCK_LLM`。** phase7 更严重——它自己的 usage docstring 明说「`EVALGATE_MOCK_LLM=0` + 真模型可跑真的」，但代码 `mock=True`（契约与实现矛盾）；phase9 干脆没有真模型路径，**Agent planner 这条唯一吃 LLM 的 agent 路径从未对真模型验证过**。
4. **smoke 退出码约定不统一，把「预期失败」和「真错误」混为一谈。** phase8/9/10/13 用 `2=断言失败 / 0=ok`；phase12 用 `1=gate fail`（而 gate fail 恰是真模型 demo 想要的结果）；phase5/6/7 只有 0（phase7 干脆无任何断言，永远 0 → 抓不到回归）。批量跑时无法机械区分好坏退出。
5. **demo eval set 太小（N=3–5），bootstrap CI 几乎判不出显著。** 真回归被淹没：phase9 quality delta −0.42 但 CI [−0.83,+0.08] 判「不显著」；phase12 quality 主轴 delta 还 +0.24（candidate 聚合分更高）只靠子项才 fail。N 这么小时主轴结论基本靠运气。
6. **可观测性/卫生（低优）**：smoke 用块缓冲 stdout，重定向到文件时 stderr 的「FAIL…」会排在缓冲 stdout 之前（phase10 日志里 FAIL 居然在报告上方，误导排查）——应 `python -u` / `flush=True` / 用 logging。另：`.phase5-runs/` `.phase6-runs/` `.phase6-variance.db` 等 scratch 既没进 `.gitignore`、phase6 也不清理，易误提交。

（明确**不算**工程问题：27B 溢出 CPU 致 phase6 跑 40min、模型拒绝越狱、faithfulness 跌幅、噪声本身——这些是硬件/模型层。但 gate 对噪声的处理方式即第 1 条，是工程问题。）

**已就地修掉的 1 个**（被 migration 0011 弄过期的断言）：`scripts/phase10_safety_smoke.py` 仍在断言 `safety.delta > 0`，但 0011 之后 safety 主轴已无独立标量（`multi_axis.py` 把 safety 主轴恒置 `delta=0.0`，`passed = not sub_regressed`，信号全在 4 个 rate 子轴）。所以该 smoke 在 mock / real 下都会误退出码 2（gate 报告本身完全正确）。改成断言「至少一个 safety 子轴显著 regress 且主轴 `passed=False`」，mock 重跑退出码 0、lint/format 绿。这正是上面第 2、第 3 条共同造成的尾巴。

**一个真模型 vs mock 的关键差异（值得记）**：mock 裁判恒定打分、模型必然"顺从"，看不到安全行为的真信号；真模型在 jailbreak 输入下会**主动拒绝**，于是 `jailbreak_compliance_rate` 真实地保持 0，而 gate 仍能靠输入侧的 `pii_input_rate` / `jailbreak_attempt_rate` 子轴抓到回归——印证了「把 safety 拆成输入/输出 4 个子轴」而不是单一布尔的设计价值。

**文档收尾**：对全仓文档做了一轮过期/重复审计并修正——README 状态行 `Phase 0–12 → 0–13` + 补 Shadow Mode/`SHADOW.md` 指引、UI「三个页面 → 四个页面」（补 Generate Trace）、`make shadow-smoke`；ROADMAP 进度图补 P13、总体节奏/推荐路线/执行守则改为「核心 0–12 + 亮点 13 已完成，下一步 14」、Phase 13 交付描述对齐真实实现（`shadow(case_input, primary=…, candidate=…)` / 按 `candidate_prompt_hash` 聚合 / on-demand rollup 非每小时）、Phase 14 迁移号 `0006 → 0013+`；给历史 plan（P5/P6/P11）加「历史快照」横幅标注已重构的死链路径（`judge/runner.py → evaluator/runner.py`、`rubric_judge.py → pointwise/pairwise`、`eval_run/repository.py → judge.persistence`），P10 把误归类到 Phase 13 的「流式 safety」更正为未排期。

**给 Phase 17 的料**：上面这批真数字（尤其 Phase 6 的 0.0853 vs 0.0503 变异度对比、Phase 12 的 124.5s / answer_relevance 归因）可直接作为复现实验底稿；design.md 第 4 节简历 bullet 的 `±15%→±3%` 仍按计划留到 Phase 17 用一组正式可复现实验回填。

## 2026-06-11 · Phase 13 · Shadow Mode（线上流量上做无害评测）

第一个**亮点 phase**。把"评测"从 PR 时刻推到**生产流量**：应用用 `await shadow(case_input, primary=…, candidate=…, sample_rate=0.1)` 包一层主调用，primary 正常返回用户，命中采样的请求**后台**并发跑 candidate（输出不返用户），用同一套 judge 给两边打分，把 `(primary, candidate)` 两条 `EvalRecord` fire-and-forget 推回 `POST /v1/shadow/observe`。滚动窗口按 `candidate_prompt_hash` 聚合，**直接复用 `build_gate_report`**（primary=baseline / candidate=candidate）出同一套四轴 + bootstrap CI + tag 归因 + `axis_breakdown` 子轴——shadow 和 PR CI 共用一份 gate 定义，零新统计代码。这是整条流水线"记录契约 = `EvalRecord`"早期投资的回报：observe 的 payload 就是它，gate 原样消费。

**两个刻意的设计取舍**：(1) **SDK 侧打分**而非后端打分——shadow 没有人工 ground truth，两边都要一个 reference-free judge 分才能成 record；放 SDK 侧复用 `build_judge_stack(primary)`（同 rubric 才公平），后端就退成纯写 + 聚合的薄层。(2) **on-demand + 显式 rollup** 而非内置 scheduler——`GET /v1/shadow/reports` 实时算、`POST /v1/shadow/rollup`（含 `evalgate shadow rollup` CLI）才落 `shadow_reports` 快照并报警，生产用 cron 调即可，本仓库不背一个定时器依赖。

**"绝不阻塞主路径"是硬约束**：采样命中 `asyncio.create_task` 起后台任务，调用方永不 await；推送 `ShadowClient` 1s 硬超时 + 双层 try 吞掉一切异常。一个真实的 asyncio 坑：后台 task 必须存进模块级 `_BACKGROUND_TASKS` 强引用（loop 只持弱引用，否则可能 mid-flight 被 GC），顺手给了 `drain_background_tasks()` 供测试/优雅关停排空。报警是 greenfield：POST 一个 `{"text"}`（Slack incoming-webhook 形状，通用 receiver 也吃），无 `EVALGATE_SHADOW_WEBHOOK_URL` 时降级 structlog warning，CI/本地零外部依赖。

**实测数字**：`make shadow-smoke` 离线（无 LLM、无 HTTP）灌 1000 条 observation、candidate cost 注入 +20%，滚动 report 四轴齐全，`cost` 轴 `delta=+0.0004` 显著 → `passed=False` → 报警触发、`alerted=True`，quality/latency/safety 保持 within tolerance。新增 20 个测试（persistence / rollup / endpoint / SDK 采样+fire-and-forget / alert），全量 342/342 + lint/format 绿；0012 migration 在本机 Postgres 上 up/down/up 验证通过（unit 测试照例走 aiosqlite，迁移由 PG 验）。

**关键技术语言**：online shadow evaluation · production-traffic A/B · fire-and-forget async with hard timeout · SDK-side reference-free scoring · rolling gate reuse（PR 与生产共用一套 4 轴）· unknown-unknown detection。

## 2026-06-11 · Phase 12 · 真实 CI Gate 端到端（替换 fixtures）

把 `eval-gate` workflow 从"seed 假 fixtures → `evalgate gate`"换成一条真 judge 流水线：seed 一个混合 reference set → 用 baseline prompt 跑一遍 judge → 用 candidate prompt 跑一遍 → diff 出四维报告。关键洞察是**一份 prompt YAML 能覆盖全部任务等价类**——`build_router` 按 YAML 里有没有 `retriever`/`rag_evaluator`/`agent_runtime` 块自动注册 generic / rag / agent evaluator，`safety` 块对所有 case 追加安全子轴。所以新建的 [`examples/ci_demo`](examples/ci_demo) 只用一个集（2 generic 含 PII+jailbreak / 1 rag / 1 agent，input 统一 `question` 键）+ 两份只差 `candidate.system` 的 committed prompt，单次 `run` 就把四条 evaluator 分支 + safety pipeline 全趟一遍。整条编排落在 [`scripts/phase12_ci_gate.py`](scripts/phase12_ci_gate.py)，CI 这步几乎是接线而非写新算法。

**mock vs real 的刻意分工**：CI 跑 `EVALGATE_MOCK_LLM=1`——离线、确定性、零 token。mock 下 pointwise judge 恒返 0.5，baseline / candidate 在同一个集上各轴完全一致 → gate 必过，所以 CI 这步本质是**端到端连通性检查**（断言每个 task_type 都产出非 error record、报告含四轴 + RAG/agent quality 子项 + safety 子项），不是抓回归。真信号留给 `make ci-gate-real`：真模型下削弱版 candidate 才会暴露质量/安全退步。这避免了 CI 烧钱、也避免拿一个本仓库无关的 PR 当"回归"误 block。

**DB 用 SQLite ephemeral**（`Base.metadata.create_all`，不跑 alembic），沿用各 phase smoke 脚本的套路，CI 不必起 Postgres service，本机/CI 行为一致。

**实测数字**：`make ci-gate` mock 端到端 ~6s 绿；`make ci-gate-real`（本机 Ollama，qwen3.5:9b 候选+裁判 / qwen3-embedding:8b 检索+ragas）一次 baseline+candidate 两轮共 8 次评测 **~140s**（远低于 5min 预算），削弱版 candidate 触发 `quality` 轴 fail，summary 点名 `answer_relevance` 子项 delta=-0.127 + 最差 tag `rag`——正是 Phase 17 录屏要的"改差 prompt → CI 红 + 归因到位"画面。

**一个 surprise（值得记）**：实现时我一度把 example/test 里的 `ollama/qwen3.5:9b` 当成"不存在的占位 tag"想换成 `qwen2.5:7b`，还顺手翻转了 [`tests/test_no_legacy_models.py`](tests/test_no_legacy_models.py) 这个守卫。跑真实验证时 `ollama list` 才发现本机装的恰恰是 `qwen3.5:9b` / `qwen3.6:27b` / `qwen3-embedding:8b`（自定义本地 tag），仓库约定和守卫一直是对的。全部回滚，只保留 Phase 12 真正的新增物，ci_demo 用本机已装的 `qwen3.5:9b` + `qwen3-embedding:8b`。教训：改"看起来是死配置"的东西前，先核对运行环境的事实（`ollama list`）。

**关键技术语言**：git-native prompt management · single-YAML multi-evaluator routing · offline-deterministic CI gate（mock judge）with a real-model escape hatch · end-to-end connectivity smoke as a CI gate。

## 2026-05-31 · 收尾 · 下线 `eval_results.safety_violation` 列（migration 0011）

一次小而干净的 schema 收尾：把 `eval_results.safety_violation` 布尔列彻底删除（[migration 0011](src/evalgate/db/migrations/versions/0011_drop_safety_violation.py)，可逆 downgrade）。

背景：Phase 10 把 safety 信号重构进了 `axis_breakdown["safety"]`（4 项速率子指标：`pii_input_rate` / `pii_output_leak_rate` / `jailbreak_attempt_rate` / `jailbreak_compliance_rate`），但当时为稳妥保留了旧的 `safety_violation` 布尔列做过渡。保留它就是"两套真理"——gate 既能看 boolean 又能看速率，语义会漂。这次把过渡列删掉，**safety 状态只有一个来源：`axis_breakdown["safety"]` 下的速率子项**。

落地影响：

- `EvalRecord` / `EvalResultRow` / `EvaluationOutcome` 都不再带 `safety_violation` 字段。
- gate 的 safety 轴判定**纯靠 4 个 sub-axis（lower-is-better）**：任一速率子项显著上升即 fail，不再依赖一个独立布尔。
- `BadCase` 的 `outlier` 策略改读 `axis_breakdown["safety"]` 任一速率 > 0（见 [`finder.py`](src/evalgate/badcase/finder.py) `_safety_metric_flags`），`BadCase` dataclass 同步去掉 `safety_violation` 字段。

**关键技术语言**：single-source-of-truth safety signal · alembic reversible column drop · gate safety axis driven purely by nested sub-metrics。

## 2026-05-16 · Phase 11.1 · UI Generate-Trace tab + `/v1/dev/seed-trace`

给 Streamlit 加了第 4 个 tab “Generate Trace”，让 ops 在浏览器里 1 次按键就能造 demo trace，省掉跑 `python -m examples.demo_app.pipeline` 的步骤。关键设计是**没把 OTel SDK 塞进 UI 进程** —— 那条路要么把 `opentelemetry-sdk` / `-exporter-otlp-proto-http` 从 dev 提到主依赖（替一个演示按钮加 3 个非平凡包），要么让 streamlit 进程同时是 OTel producer + REST consumer，污染 UI 职责。改成纯后端方案：新建 `src/evalgate/dev/trace_seeder.py` 里 `TraceSpec` pydantic 模型 + 4 个 `TEMPLATES`（rag / agent / safety / plain）+ 纯函数 `build_otlp_envelope(spec) -> dict`；新建 `POST /v1/dev/seed-trace` 路由把 envelope 交给已有的 `parse_otlp_json` + `persist_spans`。结果是 **demo trace 走的是真实 OTLP-JSON ingest 链路**（OTLP/JSON 分支以前几乎只被单测覆盖，这次顺手把它推到 ops UI 这条用户路径上），UI 只多了一个 `seed_demo_trace(spec) -> list[str]` 的 thin client 方法，零新增主依赖。

UI 表单做了模板 + 全字段可编辑：sidebar 选模板 + Apply 按钮，主区分 Connection / Root span / Retriever / Tool / LLM / Advanced 几块，每个输入都带 `help="required/optional · <用途>"` 的小字（含 `service.name` 必填、`evalgate.tag` 用于报告归因、`retriever.k` / `gen_ai.*` 落 attribute、`count` 1..20、Extra resource attributes 必须 JSON object）。session_state 存所有 widget 值，模板 Apply 是写回 session_state + `st.rerun()`。`count` 上限在 pydantic 模型层 `Field(le=MAX_COUNT)` 强约束，UI / server 两层都会拦下越界请求（FastAPI 自动 422）。

刻意的边界：**服务端不真调 LLM**。`prompt` / `mock_response` 仅作为 span attribute 落到 `gen_ai.prompt` / `gen_ai.response.content`，这避免 server 端引入 LiteLLM 运行时副作用，也让 demo trace 完全离线、幂等、确定。`examples/demo_app/pipeline.py` 保留不动 —— 它仍是“真实 OTel SDK 用户”的最小复现，与 UI demo 是两套职责。

测试两层：`test_trace_seeder.py`（12 个 unit，覆盖 4 模板的 span 结构 / parent 关系 / kind 透传 / `count>1` 产生独立 trace_id / `extra_resource_attributes` 合并 / `use_mock_response=False` 时不写 response.content / 上限拒绝），`test_dev_seed_trace.py`（6 个 ASGI integration，走完 pydantic → seeder → parser → persistence → `GET /v1/traces/{id}` 全链路）。260 → 278 passed，lint clean。

抉择：(1) **OTLP-JSON 而不是 protobuf**：`opentelemetry-proto` 虽然已经是主依赖，但手写 protobuf 比手写 dict 复杂一个数量级，OTLP-JSON 经过的 `parse_otlp_json` 是同一份 ingest 代码的姊妹分支，覆盖率反而更值。(2) **`/v1/dev/*` 而不是 `/v1/traces:demo` 或挂在 traces 路由里**：dev-only 路由命名上一眼看清不是产品 API，未来若加 auth 也好整体 gate。(3) **UI 不 import `TraceSpec`**：page 直接拼 dict 后 POST，避免 pydantic 模型在 streamlit rerun 里被反复实例化；server 端 422 已经能给 UI 兜底错误。(4) **`Home.py` 加进 ruff N999 例外**：和 `pages/*.py` 同样，Streamlit 的入口文件名约定是 capitalized `Home.py`，是 nav tab 名的唯一表达，不挪走。

## 2026-05-15 · Phase 11 · Streamlit Ops UI v1（HTTP-only，零 DB 直连）

把 trace → promote → run → 看报告的全流程从 CLI 搬到浏览器，但严格守住一条边界：**UI 进程绝不直连数据库**。Streamlit 的执行模型对 asyncio 不友好（每次交互重跑整个 page 文件），如果让它持有 SQLAlchemy async session，会跟 FastAPI 的连接池抢资源；更糟的是“UI 直读 ORM” 这种捷径会让 schema 改动同时摧毁两个 surface。所以新增 `src/evalgate/ui/` 是一个独立的 HTTP 客户端，**只**调 `/v1/*`，跟 CLI / CI 共用同一份 REST。这一次也是第一次 EvalGate 有真正的“另一个服务”消费自己的 API，反过来逼着 API 暴露了缺失的最小读路径。

那条最小读路径是 `/v1/runs*`。之前 `eval_runs` 只有写入侧（`evalgate run` → `judge.persistence.create_run / add_result / list_results`），没有 list / detail HTTP endpoint。UI Reports 页要做的事是“选两个 run → 把它们的 records 喂回现有 `POST /v1/evals/run`”，所以补了三个 endpoint：`GET /v1/runs?eval_set_id=&limit=`（latest first，可按 set 过滤）+ `GET /v1/runs/{id}`（meta）+ `GET /v1/runs/{id}/records`。第三个是关键——它把 `EvalResultRow` 直接 reshape 成 `EvalRecord`-shape JSON（`axis_breakdown` / `retrieved_contexts` 透传、`output_text` 从 `output["text"]` 抽出、`eval_run_id` / `eval_result_id` 走 `extra="allow"` 兜底），这样 UI 能不做任何二次转换把两组 records 直接 POST 回 `/v1/evals/run` 拿 GateReport。Server 端**没有规定**“谁是基线”，UI 自由组合两个 run，避免了一个隐含的方向性约定写死在后端。

UI 包内部分四块：

- **`api_client.EvalGateClient`**：同步 `httpx.Client` 包了 `list_traces` / `get_trace` / `list_eval_sets` / `get_eval_set` / `create_eval_set` / `add_case_from_trace` / `list_runs` / `get_run` / `get_run_records` / `run_gate` / `healthz` 这一组方法。所有非 2xx 响应抛 `EvalGateAPIError(status_code, detail)`，page 里 `st.error()` 拿到的就是 server 给的 `detail`，不会泄露 stacktrace 到浏览器。`base_url` 走 `EVALGATE_API_URL` env，默认 `http://127.0.0.1:8000`。同步而不是 async 是因为 streamlit 主线程是同步的，每个 page 文件每次 rerun 自己开 client / 自己 close，不维护跨 rerun 的连接池——简单胜过精巧。
- **`format` 纯函数**：`humanize_latency_ms` / `humanize_cost_usd`（区分 sub-cent 4 位 vs 2 位）/ `humanize_score`（raw vs percent）/ `humanize_datetime`（UTC ISO → `YYYY-MM-DD HH:MM`）/ `axis_status_emoji`（其实是 ASCII，遵守"不用 emoji" 项目惯例）/ `sort_attribution`（`lower_is_worse` vs `higher_is_worse` 双向）/ `format_run_label`（picker 标签）。所有可能写错的格式逻辑都被关进这里单测，**page 文件不做任何条件分支以外的格式化**。
- **三个 page**：`1_Traces.py`（侧栏 limit / service / since-hours，主区列表 + 选 trace 进 detail，右栏 span tree 用缩进 + JSON expander，底部 "Promote to eval set" 调 `add_case_from_trace`）；`2_Eval_Sets.py`（侧栏创建表单，主区列表 + 选 set 看 cases）；`3_Reports.py`（选 set → 列 runs → 双 selectbox 选 baseline / candidate → "Run gate" 按钮拉两组 records → POST gate → 4 列 metric 行 + 每个有 `sub_metrics` 的轴下挂展开表 + 按 worst delta 排序的 tag 归因表）。Reports 主区还在底部加了一个"sanity 行"——baseline / candidate 各自的 mean score / sum cost / avg latency，让 UI 自检 gate 报告跟原始 records 对得上。
- **`Home.py` landing**：set page config、画 `/healthz` 状态徽章、列 page 用法。

测试矩阵故意全 offline。`test_runs_endpoint` 用 conftest 的 in-memory aiosqlite 起一个 FastAPI app，测 list / set 过滤 / limit / 404 / detail 透传。`test_runs_records_endpoint` 测 `EvalResultRow → EvalRecord` 的字段映射 + `axis_breakdown` 透传 + 端到端把两组 records 喂回 `POST /v1/evals/run` 看 GateReport（`{quality, cost, latency_p95, safety}` 四轴齐全）。`test_ui_api_client` 用 `httpx.MockTransport` 拦截，验证 client 发的 URL / params（`None` 必须被剥掉，否则 `?service=None` 会污染 server 过滤）/ pydantic 解析 / 错误码 → `EvalGateAPIError` 的非 JSON 兜底。`test_ui_format` 是纯函数 unit。**没有**起 streamlit headless 跑 page——streamlit 的 page rerun + session_state 半持久化模型不适合断言，渲染断言性价比极低，用 page 模块 import 不带副作用 + 把所有有判断逻辑的代码下沉到 `format.py` 来代替。

几个抉择记一笔：

1. **`/v1/runs*` 拆到 `evals.py` router 而不是新建 `runs.py`**：`evals.py` 已经在管 “运行评估” 这个语义，list / detail / records 都是 “某个 run 的数据视图”，本就是一类东西。Mount 时给它两个 tag（`evals` + `runs`）让 OpenAPI 仍能按 namespace 分组。
2. **`get_run_records` 直接 reshape 到 `EvalRecord` 而不是新发明 schema**：gate `build_gate_report` 已经吃 `EvalRecord`-shape dict 了，重新发明一个 `EvalResultOut` 等于让 UI 多走一层映射。`EvalRecord` 是 `extra="allow"`，所以可以塞 `eval_result_id` / `eval_run_id` / `output_text` / `retrieved_contexts` 这些 row-only 字段而不破契约——以后 Phase 12 的 attribution 想多看一些 trace 字段，加进 record 同时 list 端口免费跟上。
3. **同步 client 而非 async**：streamlit 的 page 文件每次 rerun 都从顶到底重新执行，async client 要么 `asyncio.run` 浪费一次 event loop 启动，要么显式 `nest_asyncio`，两条路都加噪音。同步 + per-page client + `with` 上下文关闭是当下最朴素的选择。
4. **不做"一键 evalgate run" 按钮**：放到 Phase 12+ 跟 CI gate 替换 fixtures 一起做，那时候才有 worker 异步执行 eval 的需求。Phase 11 守住"看而不动"的边界，UI 真正的副作用只有两个：创建 eval set + promote trace → case，全是已有 Phase 4/7 写过测试的端口。
5. **`pyproject.toml` 把 `httpx` 从 dev 提到主依赖**：`api_client` 在生产代码里 import httpx，再放 dev group 就不一致了。`pytest` / `pytest-asyncio` / `aiosqlite` 这些只在测试用的还留在 dev。

测试结果：242 passed, 13 warnings in 6.82s（warning 全是 LiteLLM 的 `coroutine 'Logging.async_success_handler' was never awaited`，上游 issue，无功能影响）。`make ui` + `evalgate-api` 双进程本机起来浏览器走通 trace → promote → run → reports 全流程，Reports 页在 demo seed 数据上能正确同时渲染 quality 的 ragas sub-axes 和 safety 的 4 个 sub-axes。

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
- **真 ragas + LiteLLM adapter**：用户明确选 A 路径——保留 RAGAS 品牌而不是自己重写 prompts。`ragas_adapter.py` 写一个 `LiteLLMChatModel(BaseChatModel)` + `LiteLLMEmbeddings(Embeddings)` shim 让 ragas 的 langchain 调用全部走我们已有的 `litellm.acompletion` / `aembedding`。`mock_text` / `mock_mode` 短路给单测和 CI 用，hash 384-dim 伪向量保证不连 Ollama 也能跑。一个 `_RagasScorer` 把 `ragas.evaluate(Dataset.from_dict(row), metrics=[...])` 包成 async（`run_in_executor`），结果转 `JudgeCallRecord(judge_model="ragas:<metric>")` 落 `eval_judge_calls`，Phase 16 calibration 直接复用。
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

1. **不加新表**：Phase 7 全是 SELECT，LLM 标签也不缓存。决策清晰度 > 性能微优化；Phase 16 calibration 真要复用 LLM 标签再加 `bad_case_labels`。
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
3. **`eval_judge_calls` 一行一次原始调用**：N×K×P 行/case 全落库，Phase 17 算 κ、Phase 16 算 ECE 直接 SQL，不再回放 judge。`eval_results.judge_confidence` 真填了，gate / BadCase 现在可用。
4. **`case.expected` 在 pairwise 模式下硬必需**：缺失 → emit `error=True, error_kind="missing_reference"` 的 EvalRecord，**不静默 fallback 到 pointwise**。失败显式可见，胜过埋雷。
5. **Confidence 公式两层乘**：`per_judge_conf = 1 - std/0.5`（self-consistency 内部稳定度）× `cross_term = 1 - cross_std/0.5`（judge 间一致度）。两层都满 → 1.0；任一层崩 → 接近 0.0。最大 std=0.5 来自分数 ∈[0,1] 的几何上界，让 confidence ∈[0,1] 直接可解释。

栈的拓扑：`Runner → MultiJudge(N) → SelfConsistencyJudge(K) → PositionSwapJudge(P) → PointwiseJudge | PairwiseJudge`，单 case 内 `N×K×P` 次走 `asyncio.gather + Semaphore(concurrency)`，跨 case 仍然顺序（保留 Phase 15 stream）。Temperature 自动 bump：K>1 且用户没设 → 强制 ≥0.7（K=1 不动），否则 greedy decoding 让方差信号塌成 0，confidence 公式失效。

**Tech**: Pydantic v2 `model_validator(mode="before")` 拦截 legacy 字段、`statistics.pstdev` 做总体方差、`asyncio.Semaphore` 限速、`response_format={"type":"json_object"}` 提示 JSON 输出、SQLAlchemy ORM + JSONB on PG / JSON fallback on SQLite。

**Test 数量**：原 19 + 新 7 (`pointwise / pairwise / position_swap / self_consistency / multi_judge / judge_calls_persistence / runner_multi_judge`) = **99 全绿**。lint clean。Phase 5 三个测试文件 + Phase 5 candidate test 一并迁移到新 schema。

**详细方案**：[docs/PHASE_6_PLAN.md](docs/PHASE_6_PLAN.md)
**Commit**: 待 commit。

## 2026-05-14 · Phase 5 · LLM-as-Judge Runner v1（LiteLLM + 本地 Ollama）

把 Phase 4 攒下的 eval_set 真正"跑起来"。新增 [src/evalgate/judge/](src/evalgate/judge/) 五件套（`prompt_spec` / `candidate` / `rubric_judge` / `persistence` / `runner`），加 `evalgate run --eval-set X --prompt p.yaml --out r.json` CLI 子命令，落两张新表 `eval_runs` / `eval_results`（0004 migration），输出 JSON 直接喂 Phase 2 的 `evalgate gate`。本地用 **qwen2.5:7b（Ollama）** 真跑通：3 条 billing case，baseline vs candidate 两次 run，4 轴 gate 报告齐活，候选弱化 prompt 让 latency_p95 从 12.7s 掉到 1.3s，验证了 latency 轴的真信号。

几个值得记的设计取舍：

1. **Rubric 放进 `prompt.yaml`，不进 eval_set**：评分标准跟候选 prompt 一起在 git 里 diff，避免在 DB 里维护"通用 rubric"的复杂度。
2. **Runner 写成 `iter_eval` 流式 + `run_eval` 薄包装**：Phase 15 Sequential Gate 直接消费 stream 做 early-stop，无需重构。
3. **`EvalResultRow` 预留 `judge_confidence` + `judge_raw`**：Phase 16 Calibration 重算 ECE 不需要重跑 judge、不需要再发 migration。
4. **`EvalRecord` 落到 `core/schemas.py` 当固化契约**：Phase 13 Shadow Mode 的 `/v1/shadow/observe` 直接 import 复用，不会出现字段名漂移。
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
