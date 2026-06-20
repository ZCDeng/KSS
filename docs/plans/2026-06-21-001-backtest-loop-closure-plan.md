---
type: feat
origin: docs/ideation/2026-06-21-backtest-loop-ideation.html
status: plan
created: 2026-06-21
execution: code
---

# KSS 回测闭环补强 — 统一实施计划

把 7 份 requirements 合成为一条有依赖顺序的实施线：**信任地基 → 共享账本 → 度量闭环 → 强验证与发现 → 探索性门控**。一句话目标：把 KSS 已有的、各自孤立的回测/校验诊断能力，接成一个**会自我纠偏的闭环**，服务「跟踪自选」+「发现潜力股」。

来源 requirements（7 份，均已核实真实接口）：
- `docs/brainstorms/2026-06-21-feature-lookahead-guard-requirements.md`（U1）
- `docs/brainstorms/2026-06-21-prediction-lifecycle-ledger-requirements.md`（U2）
- `docs/brainstorms/2026-06-21-factor-health-loop-requirements.md`（U3）
- `docs/brainstorms/2026-06-21-fix-daily-review-forecast-requirements.md`（U4）
- `docs/brainstorms/2026-06-21-cpcv-backtest-requirements.md`（U5）
- `docs/brainstorms/2026-06-21-discovery-merge-layer-requirements.md`（U6）
- `docs/brainstorms/2026-06-21-kronos-shadow-judge-requirements.md`（U7）

---

## 问题框架与范围

KSS 回测地基已硬（walk-forward、purge、DSR、8 层 bias 剥离、log_mv 反向 Sharpe 1.74、每周 `validate_predictions`、每日纸交易）。缺口是**结论不回流**：诊断是一次性手工分析，不会自动改进模型/阈值/发现逻辑。本计划补的是「闭环」本身，不是再造一个回测器。

**范围内**：度量基建（IC/ICIR/崩盘库）、统一预测账本、修复已触发停用线的 daily_review、堵 feature 泄漏、发现管道合并与管道级 alpha、CPCV 强验证、Kronos 影子门控。
**范围外**：新交易执行/下单、新数据源接入（除非现有）、Swift 前端大改（仅必要的读取展示）、把外部基模型直接当选股器（TimesFM 先例已否决）。

---

## 全局技术决策（跨所有单元的不变量）

1. **PIT 纪律是硬红线**：外部快照/实时数据严禁回流回测（非 PIT → look-ahead/幸存者偏差）。任何新数据进回测必须能证 PIT 清白，举证在使用方。
2. **金融数字一律代码确定性渲染**：LLM/归因文本只给定性标签，真值由代码追加（龙虎榜事故先例）。归因 prompt 禁止注入价格字段。
3. **复用而非重建**：`validate_predictions.py`、`paper_trade_log_mv.py`、`kss/backtest`（`walk_forward`/`significance.deflated_sharpe`/`is_deployable`/`bootstrap_ci`）、`ModelRegistry`、`kss/notifications/manager.send_to_channels`。
4. **阈值外化 + walk-forward 标定**：所有新阈值（IC 门、ICIR 基线、regime 切换、管道权重）写 YAML/JSON 配置并经 walk-forward 标定，不硬编码。
5. **有效 n = 去重交易日数**，不是事件数（A 股横截面高相关，etf_flow 教训）。IC 显著性检验沿用 `significance.t_stat`/`newey_west_se`。
6. **单用户本地工具**：算力有限，避免全市场全因子每日重算；重计算可缓存、可配规模、可降级。
7. **新功能默认关闭、影子先行**：凡可能影响已通过 DSR 的 log_mv 基线的（U7 弃权门、U4 区间改动），默认 flag off，先影子/对照，证明边际再开。
8. **IC 双源仲裁**（防止「板块复盘 vs 热点」分歧在度量层重演）：系统存在两条 IC——回测分布 IC（U5 CPCV，历史大样本）和实盘账本 IC（U3，近期小样本）。仲裁规则：
   - **回测 IC 为先验，实盘 IC 为更新**；
   - 因子/管道**只在两者都同意时升权 / 置 ACTIVE**；
   - **任一越线即降权 / 置 PENDING_REVIEW**（fail-safe 偏保守）；
   - 实盘 IC 在低于其有效 n（去重交易日数，见 #5）时**仅供参考**，不得单独翻转状态，回退先验；
   - 实盘与回测**符号分歧**记入 `FactorCrashRegistry` 的 `IC_SOURCE_DIVERGENCE` 事件，人工复核，绝不静默解决。
   该规则是整个闭环「会自我纠偏」的前提——没有它，U6 的管道权重在分歧下不确定、U3 的退役状态机可能误判。

---

## 依赖图与排序

```
Phase A  U1 feature-lookahead-guard      （粗泄漏检测，无依赖）   ┐ 可并行
         U2(F2 结算) prediction-ledger    （真实开盘价结算，无依赖）┘
            │（U2 的 F3 归因层依赖 U1 的可信 IC）
Phase B  U2(F3 归因) + 账本退役收口
            │
Phase C  ├─ U3 factor-health-loop        （IC/ICIR/崩盘库；依赖 A+B）
         └─ U4 fix-daily-review-forecast （校准；依赖 B，可与 U3 并行）
            ├──────── 价值检查点（见下）────────┐
Phase D  ├─ U5 cpcv-backtest             （仅依赖 U1；默认 15 路径；产物=回测 IC 先验）
         └─ U6 discovery-merge-layer     （合并逻辑可早交付；管道 alpha 依赖 U3）

Deferred Spike  U7 kronos-shadow-judge   （移出主线：PIT 举证未过 + 影子语料 n=1，详见 Deferred）
```

**关键依赖说明（经 doc-review 修订）**
- **U1 与 U2 结算可并行**：U2 的「对错」(`realized_ret`) 用真实 T+1→T+2 开盘价（`_horizon_return`），**不碰回测**，故 U2 的 F2 结算不依赖 U1。真正依赖 U1 的是 U2 的 **F3 归因层**（`factor_stale` 类别用 IC 滚动均值，IC 必须来自无泄漏回测）。若按 OQ-3 先只发 `data_missing/factor_valid` 归因，U2 可与 U1 完全并行。
- **U1 是「粗泄漏检测」不是「信任地基」**：它只可靠拦截近似复制 label 的特征（IC≈1）；时序泄漏（当日收盘价用于开盘）IC 可能仅 0.1–0.4，**过得了 0.95 闸**（实测 close-at-open IC=-0.008）。「可信回测」= U1 粗检 + purge/embargo + U5 OOS 分布**三者合力**，U1 单独不成立（见 U1 单元修订）。
- **U2 是底座**：U3/U4/U5/U6 都从账本取数；账本退役收口（旧 paper_trade 冻结只读）在 Phase B 完成。
- **价值交付剖面**（doc-review product-lens）：U1–U3 交付的是**信任与度量**（用户无感）；第一个**用户可见**收益是 U6（发现潜力股）与 U4（修复当前正在误导用户的死功能）。最小闭环 = **U1+U2+U4**（两 S 一 M），先交付并验证账本「值不值得养」，再决定是否进 Phase D 的重单元（U3/U5/U6）。U6 的**合并+去重**半截可在 Phase C 就对现有四管道交付，管道 alpha 加权半截再等 U3。

---

## 实现单元

### U1 — Feature 泄漏防护（粗检 + 时序检查）+ 对抗测试转正
- **定位（doc-review 修订）**：U1 **不是**「信任地基」，是**两道互补的泄漏检测**。单一 IC 阈值只抓「近似复制 label」的粗泄漏；真正咬人的是**时序泄漏**（用当日收盘价做开盘信号，IC 只有 0.1–0.4 却照样虚高 Sharpe），磁单靠相关性阈值抓不到。故 U1 = 粗检 + 时序检查双管。「可信回测」由 U1 + purge/embargo + U5 OOS 三者合力，不可只凭 U1 通过就下放完全信任。
- **Goal**：`walk_forward` 入口拒绝（a）近似复制 label 的特征与（b）时序不可得特征；`test_adversarial.py` 的 3 个 xfail 翻成 pass。
- **Files**：改 `kss/backtest/engine.py`（`walk_forward` ~273 加 `FeatureLookaheadGuard.check()`）；新 `kss/backtest/lookahead_guard.py`；改 `kss/tests/test_adversarial.py`；配置 `lookahead_whitelist` + 阈值。
- **Approach**：
  1. **粗检**：全 panel Spearman IC，特征与 `label_col` IC>0.95 抛 `LookaheadFeatureError`（拦复制 label）。
  2. **时序检查（关键）**：① 可得性——每个特征列必须可由 `trade_date <= decision_date` 的数据推出（声明式标注或断言）；② **label 平移敏感度**——把 label 前移 ±1 bar 重算 IC，若 label **前移一格**时 IC 不降反升/不显著下降，说明特征在吃同期/未来信息（即便绝对 IC 中等），标记泄漏。
  3. 白名单为逃生口（warning 不异常），阈值/名单调用方可覆盖。
- **Test scenarios**：注入 `next_day_return` 衍生特征→粗检抛错；**close-at-open 时序泄漏特征（IC≈-0.01）→ 平移敏感度检测捕获**（这是粗检漏掉、新检查必须抓的关键用例）；白名单内→仅 warning；正常特征→放行；三 seed 对抗测试转 pass。
- **Verification**：`pytest kss/tests/test_adversarial.py` 全绿；**构造一个 IC<0.95 的时序泄漏特征也被拒**（证明不只是复制检测器）；文档明确「U1 单独 ≠ 可信回测」。
- **Dependencies**：无。可与 U2 结算并行。**Execution note**：characterization-first（先固化现有 xfail 行为再让 Guard 翻正）。

### U2 — 预测生命周期账本
- **Goal**：单一账本记录每条预测的「入选→结算→结构化归因」，下游复盘/IC/管道 alpha 统一取数。
- **存储选型（doc-review 定，移出 blocking）**：**SQLite**（按 date/symbol/status 可索引查询，与既有 `storage/kss_quotes.db` 先例一致；滚动 IC 查询需索引，NDJSON 不胜任）；如需纯追加审计可加 NDJSON 旁车。
- **Files**：新 `kss/prediction/ledger.py` + `storage/prediction_ledger/ledger.db`；接 `scripts/paper_trade_log_mv.py`（`save_log_entry` 加 `pipeline_snapshot/regime_label/status`）；`scripts/kss_app_bridge.py`（`_recommendation_tracking` 改读账本）。
- **Approach**：主键 `{date}_{symbol}`；三段流 F1 入账（因子值/管道/regime）→ F2 结算（`_horizon_return` 的 T+1→T+2，**真实开盘价、代码渲染真值**）→ F3 归因（5 级规则路由：data_missing>factor_stale>regime_shift>execution_friction>factor_valid）；LLM 归因文本禁注价格字段。
- **账本退役收口（doc-review 修订，消除永久 dual-write 债）**：迁移是过渡态，须有终止条件——回放把全部历史 `paper_trade/*.json` 回填进账本（由验证项的重放测试确认）后，`paper_trade` JSON **冻结为只读归档**；`_recommendation_tracking` 对**已结算与未结算都读账本**（未结算记录以 `status=open` 存在账本里，不再回退旧存储）。fallback 是账本内 status 查询，不是第二套存储系统。
- **Test scenarios**：入账→结算→归因全链路；T+2 未到时 `status=open` 不误结算；归因分类命中；回放历史 `paper_trade/*.json` 重建账本且与 `_horizon_return` 对齐（退役前置）。
- **Verification**：重放对齐 `_horizon_return`；归因字段无 LLM 幻觉数字；退役条件可判定（回填完成→旧路径只读）。
- **Dependencies**：**F2 结算无依赖**（真实开盘价）；**F3 归因依赖 U1**（`factor_stale` 用的 IC 须来自无泄漏回测）。可按 OQ-3 先发 `data_missing/factor_valid` 归因 → 此时 U2 与 U1 完全并行。**Execution note**：test-first（结算/归因纯函数先测）。

### U3 — 因子健康度闭环（滚动 IC/ICIR + 崩盘登记库）
- **Goal**：因子/信号逐期 Rank-IC/ICIR 落库，衰减触发告警/待复核/可剔除；回测崩盘结构化入库。
- **Files**：新 `kss/backtest/factor_health.py`（`FactorHealthTracker`、`FactorCrashRegistry`）；hook 进 `walk_forward_combiner.py`/`cross_section.py` 回测结束点；复用 `significance.t_stat/newey_west_se`、`notifications.manager.send_to_channels`；阈值 YAML。
- **Approach**：滚动窗口 Rank-IC + ICIR + 1d/5d/20d 衰减半衰期；状态机 ACTIVE→PENDING_REVIEW→RETIRED（剔除留人工确认）；有效 n=去重日期数。
- **IC 双源边界（doc-review 关键澄清）**：U3 算的是**实盘账本 IC**（用 U2 `realized_ret`，反映执行后真实表现）——它是仲裁规则 #8 里的「更新/后验」，**不是先验**。状态迁移必须走 #8：实盘 IC 单独只能**降权/PENDING_REVIEW**（fail-safe），**升权/RETIRE 需与 U5 回测先验一致**；实盘 IC 低于有效 n 时仅供参考。**`严禁回流回测` 的精确边界**：实盘 IC 可读账本结算收益做**因子诊断**，但诊断结果（阈值/状态）**不得直接改已验证的回测参数/特征工程**；要改回测参数须独立 walk-forward 重标定。
- **Test scenarios**：人造衰减序列触发 PENDING_REVIEW；实盘 IC 想升权但回测 IC 不认→不升权（#8）；实盘 IC 符号与回测分歧→记 `IC_SOURCE_DIVERGENCE`；有效 n 按去重日期；崩盘记录可查询。
- **Verification**：对 log_mv 历史回放，实盘 IC/ICIR 滑轨口径正确；升权路径必须双源一致才放行；衰减告警可达 console/telegram。
- **Dependencies**：U1（粗检+时序，喂回测 IC 先验的可信度）、U2（结算收益源）、**仲裁规则 #8**（与 U5 协同）。

### U4 — 修复 daily_review 次日预测（校准优先）
- **Goal**：把 ≈随机的次日情形分布修到过停用线，或按判据可执行地撤段。
- **Files**：改 `kss/prediction/`（`scenario_distribution()`、`adjusted_scenarios()` 加 regime 参数、`_advice_block()` L556–561）；`scripts/daily_review*`；`validate_predictions.py` 接 `SCENARIO_ENABLED` flag。
- **Approach**：① 区间以全历史无条件 P10/P90 兜底；② regime 开关——**复用现有 `kss/sector/momentum_regime.py`（`build_regime_status` 给 `in_regime/mom20`）**，不新建，均值回归先验在动量 regime 关掉；③ 删常量「5–10 日仍看涨」；④ 止损改仓位语义；⑤ Brier 拆校准/分辨定位；`SCENARIO_ENABLED` 让撤段可执行。
- **U2 取数（doc-review 补明确）**：U4 读账本 `realized_ret` + `outcome`（win/loss/flat）字段，按 regime 与预测日期聚合做校准统计；不读价格细节。
- **Test scenarios**：无条件兜底加宽 80% 覆盖率；regime 开关在动量段抑制反向先验；撤段 flag 关闭后该段不渲染；校准/分辨分解数值正确。
- **Verification**：重放校验集 Brier<0.8 且方向命中≥45%（过停用线）；否则 flag off 撤段且不误导。
- **Dependencies**：U2（结果取数/校准统计）；regime 复用 momentum_regime（非新建）。**Execution note**：characterization-first（先记录当前随机基线再改）。

### U5 — CPCV 组合净化交叉验证
- **Goal**：回测从点估计升级为 OOS 分布，输出**回测 IC 分布**（仲裁 #8 的「先验」）+ Sharpe 分布，喂进现有 DSR/PBO 门槛。
- **Files**：新 `kss/backtest/cpcv.py`（`CPCVBacktester`），上游包 `WalkForwardCombiner`，不改现有接口；缓存 `storage/cpcv_cache/{run_id}/fold_{i}.pkl`。
- **Approach**：生成 C(k,p) 净化折叠；**验收基线 = 默认 k=6/p=2=15 路径**（实测 ~125s）；聚合 Sharpe + **IC 分布**(per path 算 IC/ICIR，供 #8 与 U3/U6 用) + PBO；`deflated_sharpe(n_trials=n_paths_valid)`、`is_deployable(strategy_family="mined")`。purge/embargo 防泄漏。
- **规模说明（doc-review 校正）**：210 路径实测约 **29min（非「单机偏重」，是可接受的批跑）**，但默认 15 路径是为**迭代速度**而非可行性；210 严格模式标 **deferred**，不作首版验收门。fold 缓存若为达 15 路径时延目标所必需则随单元交付（非可选）。
- **Test scenarios**：折叠数=C(k,p)；purge/embargo 边界无泄漏（含 `_rolling_zscore` 严格因果性）；IC 分布产出供 #8 仲裁；路径分布喂 DSR 合理。
- **Verification**：对 log_mv 跑 15 路径 CPCV，p50 Sharpe + IC 分布与单一 walk-forward 一致量级且更稳健；时延 ≤ ~3min。
- **Dependencies**：U1（purge/时序正确性地基）。可与 Phase B/C 并行，排 D 做风险批次。

### U6 — 统一发现管道合并 + 管道级 alpha（分两截交付）
- **Goal**：四发现管道（log_mv/北证/板块热度/紫苏叶）合并去重 + 共识加权；各管道按回测 alpha 配权，替硬编码阈值。
- **分截（doc-review 修订，先交付用户可见价值）**：
  - **截 1（可在 Phase C 早交付，不依赖 U3）**：合并 + 去重 + 共识溢价。**先做在 `kss_app_bridge.py` 内的 `_discovery_merge()` 私有函数**（四管道已在 bridge 汇聚，单一消费者不值得新建包）；bridge 命令 `get-discovery-candidates`。
  - **截 2（依赖 U3/U5）**：管道级 alpha 加权——离线 `scripts/compute_pipeline_alpha.py` → `storage/pipeline_alpha/`、`storage/pipeline_weights.json`，用 U3 的 IC 基建逐管道算 top-quintile alpha；权重更新人工确认。
  - `kss/discovery/` 独立模块**推迟**到出现第二个消费者再抽。
- **相关性预检（doc-review 补回 ideation 漏掉的护栏）**：共识加权前先算**管道两两命中相关性**；A 股横截面高相关下，相关管道的「共识」会**放大共同偏差**而伪装成独立确认。`consensus_multiplier=1.2` 溢价须**门控在已证独立性**上（高相关时下调甚至取消溢价）。
- **Test scenarios**：多管道命中升权；跨板块重复去重；板块管道（无 ts_code）成分股展开；**管道相关性预检：高相关时溢价被抑制**；管道 alpha 计算可行（依赖 log_mv 日归档存在）。
- **Verification**：合并候选集相对单管道在回测里 IC 不劣化；管道权重由实证 alpha 排序；相关管道不享受共识溢价。
- **Dependencies**：截 1 仅需 U2；截 2 需 U3（IC 基建）+ 仲裁 #8（管道 alpha 走双源）。受益于 U5。

### U7 — Kronos 影子裁判 ⟶ **移出主线，降为 Deferred Spike**（doc-review 一致建议）
**为什么移出**：① 现有 `storage/kronos/predictions.sqlite` 实测仅 **3 股 / 单一 base_date / 有效 n=1**——「正边际才启用」**根本无法评估**（违反 Fail-loud：不该立一个测不出的验收门）；② PIT 清白举证**可能永远过不了**（基模型 cutoff 未披露）；③ shadow cron「需从头搭」，是纯新建基建喂一个上限封顶的探索。④ `kss/kronos/` 零 `.py` 源码、与 U1–U6 完全解耦——留在主线只会制造一个永远「做没做完」答不清的假 Phase。

**作为 spike 的可测前置条件**（替掉原来不可测的验收）：U7 **不开工**直到——
1. Kronos 基模型 cutoff 披露、或找到可证 PIT 清白的代理；**且**
2. 影子推理路径已搭、账本积累 **≥ N_min 去重交易日**的 Kronos U(t) 记录（N_min 按其余门同一 walk-forward 标准定）。
两条都满足前：不计算边际、弃权门**不可构建**（不只是 flag off）。现有 15 行 sqlite 仅作 schema/smoke 夹具，**不是**度量语料。

详见 `docs/brainstorms/2026-06-21-kronos-shadow-judge-requirements.md`；当 U2 账本就绪后该 spike 可独立取数，无需占用本计划交付路径。

---

## 风险与依赖（doc-review 修订）

- **闭环成立的硬前提 = 仲裁规则 #8**：没有它，实盘 IC（U3/U6）与回测 IC（U5）分歧时谁说了算未定，闭环无法自我纠偏——这是把「板块复盘 vs 热点」分歧在度量层重演的风险，规则 #8 是堵口。
- **U1 不是万能信任地基**：单 IC 阈值只抓粗泄漏，时序泄漏须靠新增的可得性 + label 平移检查，且最终信任由 U1+purge/embargo+U5 三者合力。
- **U4 可能修不好** → 已有停用判据 + `SCENARIO_ENABLED`，修不好就撤段，不是死路。
- **U6 截 1 vs 截 2**：合并去重半截早交付（仅依赖 U2）；管道 alpha 半截才依赖 U3。**板块管道无 ts_code、log_mv 日 CSV 归档是否存在** 影响截 2，需起步确认。
- **U7 已移出主线**（Deferred Spike），不阻塞 U1–U6。
- **算力非瓶颈**：U5 15 路径 ~125s、210 路径 ~29min（可接受），默认 15 为迭代速度。

## System-Wide 影响
- 新增 `storage/prediction_ledger/ledger.db`、`storage/pipeline_alpha/`、`storage/pipeline_weights.json`、`storage/cpcv_cache/`、因子健康/崩盘库 → 纳入审计底稿（如 etf_radar 例外）。
- **`严禁回流回测` 的精确边界**：实盘账本 IC 可读结算收益做**因子诊断**；但诊断结果（阈值/状态）**不得直接修改已验证回测参数/特征工程**——要改回测参数须独立 walk-forward 重标定。账本数据**绝不**注入 `factor_df`/特征工程。
- 回测入口加 Guard 会让现存偷看未来的特征**直接报错**——可能暴露既有隐藏泄漏（好事，但首跑要逐一甄别白名单）。

## 交付批次与价值检查点（doc-review）
- **第一批（最小闭环）= U1 + U2 + U4**（两 S 一 M）：可信粗检 + 共享账本 + 修复当前正在误导用户的死功能。先交付、验证账本「值不值得养」。
- **价值检查点**（Phase C 后）：若 U2 账本 + U4 修复没明显改善日常工作流，**重新评估** U3/U5/U6 是否值其维护面再进 Phase D（单人维护工具，维护面是长期主成本）。
- **第二批** = U3 + U5 + U6（度量深度 + 发现合并），按检查点结论推进。

## Scope Boundaries / Deferred
- **Deferred**：U7 Kronos 整体降为 spike（待 PIT 举证 + 影子语料 ≥N_min）；CPCV 严格 210 路径常态化（先 15 路径验收）；`kss/discovery/` 独立模块（先 bridge 内函数）；前端对账本/健康度可视化（先 CLI/bridge）。
- **Out**：交易执行、新外部数据源、把基模型当选股器。

## Implementation-Time Unknowns（剩余，已收窄）
- **已定，移出 blocking**：账本存储=SQLite；regime 复用 `kss/sector/momentum_regime.py`。
- **仍需起步确认**：`_rolling_zscore` 严格因果性（U5）、板块管道成分股展开归属与 log_mv 日归档存在性（U6 截 2）。
