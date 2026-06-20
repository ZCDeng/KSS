---
module: kss/backtest
tags: [adversarial-testing, bias-detection, known-gaps]
problem_type: quality-assurance
date: 2026-05-12
source: kss/tests/test_adversarial.py
---

# KSS 已知偏差检测 Gap 清单

由 `kss/tests/test_adversarial.py` 对抗性测试套件揭出的 KSS 工具
**当前防不住**的偏差。后续可按优先级补工具。

## 测试结果总览

| 场景 | 工具表现 | 状态 |
| --- | --- | --- |
| 1. 纯随机噪声 | `Significance.deflated_sharpe` 稳稳拒绝；`cross_section_ic_scan` |t| 受控；50 次 Type-I 错误率 ≤ 20% | PASS |
| 2. 特征级 look-ahead | `FeatureLookaheadGuard` 入口粗检（复制 label）+ 时序检查（label 平移敏感度）（U1） | **RESOLVED** |
| 3. 单股噪声伪 alpha | `cross_section_ic_scan` 自动稀释，揭穿 | PASS |
| 4. 末段集中回撤 | 60 日滚动 Sharpe / `max_dd` / `calmar` 联合警示 | PASS |
| 5. 同质化多因子 | `cross_section_ic_scan` 显假象，但因子相关矩阵 `panel[fs].corr()` 揭穿 | PASS |
| 6. 幸存者偏差 | `ExecutionModel + SuspensionData` 停牌名单显式 PIT 过滤（4.2） | **RESOLVED** |

最终：对抗套件 0 xfail / 0 fail（U1 后场景 2 三 seed 由 xfail 转 pass）.

## Gap 1：Feature-level Look-ahead —— **RESOLVED 2026-06-21（U1）**

**原现象**：`walk_forward` 的 `purge_gap=N` 只剔训练区间尾部 N 天的 `future_return_Nd`，
**防的是 label leak**。若使用者把 `next_day_return`（或衍生的未来值）放进
`feature_cols`，test 时该 feature 依旧泄漏未来 → 模型在 test 上作弊，sharpe 爆表。

**修复方案（U1）**：`kss/backtest/lookahead_guard.py` 的 `FeatureLookaheadGuard.check()`，
在 `walk_forward` 入口（任何数据切分前）跑两道互补检查：
1. **粗检** —— feature 与 `label_col` 全 panel |Spearman IC| > 0.95（严格 `>`）→ 抛
   `LookaheadFeatureError`（拦近似复制 label）。
2. **时序检查** —— label 平移敏感度：symbol 内把 label 前移一格得「当期对齐」对照，
   feature 对当期对齐 label 的 IC 量级显著超过对预测 label 的 IC 且非平凡（≥0.10、超
   预测 IC × 1.5、配对样本 ≥100）→ 判时序泄漏抛错。抓 IC<0.95 的 close-at-open 型同期
   信息（粗检漏掉的关键用例）。

**逃生口**：`lookahead_whitelist` 命中只 `logger.warning` 不抛错；`lookahead_guard=False`
或 `lookahead_ic_threshold=float("inf")` 可关检查（研究/调试）。阈值经
`config/settings.yaml` `backtest.lookahead_guard` 外化。

**「U1 单独 ≠ 可信回测」**：粗检 + 时序检查只是地基之一；完整信任 = U1 + purge/embargo
+ CPCV OOS 三者合力（见 `docs/plans/2026-06-21-001-backtest-loop-closure-plan.md` U1 段）。

**验证**：`test_adversarial.py` 场景 2 三 seed 由 xfail 转 pass（断言抛
`LookaheadFeatureError`）；新增 `test_lookahead_guard_temporal_leak_under_threshold`
（IC<0.95 时序泄漏被拒）、`test_lookahead_guard_normal_feature_passes`、
`test_lookahead_guard_whitelist_passes`、`test_lookahead_guard_threshold_boundary`。
入口：`kss/backtest/engine.py::BacktestEngine.walk_forward`。

## Gap 2：Survivorship Bias —— **RESOLVED 2026-05-12**

**原现象**：`BacktestEngine.walk_forward` 的 `test_df.dropna(subset=...)` 会静默
丢弃退市 / 停牌股，结果只看到"幸存者"的表现。

**修复方案（4.2 实施，参考 Qlib `qlib/backtest/exchange.py`）**：

1. 新增 `kss/data/suspension_data.py::SuspensionData`：从 CSV
   （`storage/suspension_dates.csv` + `storage/st_dates.csv`）加载停牌 / ST 区间，
   提供 `is_suspended` / `is_st` / `filter_panel`. CSV 不存在 → 空 set + info log
   （向后兼容）.
2. 扩展 `kss/backtest/cost_model.py::ExecutionModel` —— 新增 kwarg-only 字段
   `suspension_data` / `exclude_st` / `exclude_suspended` / `exclude_zero_volume`，
   并在 `filter_tradable` 末尾叠加停牌 / ST / 零成交（amount=0 视作隐式停牌）过滤；
   同时新增 `is_tradable` 行级综合判定 API.
3. `factor_cross_section_backtest` 已通过 `execution.filter_tradable` 受益；
   旧调用方未传 `suspension_data` 时行为完全不变.
4. 可选 Tushare 拉数：`fetch_suspension_from_tushare`（接 `suspend_d`）、
   `fetch_st_from_tushare`（接 `namechange`）；需 5000 积分，积分不足时
   `log.warning` + 返回 `False`（不抛）.

**测试结果**：

- `test_survivorship_bias_inflates_returns` 从 xfail → pass（seed=0/7）.
- 原始对照仍验证 raw_gap > 0.3（fixture 防退化）；引入 `SuspensionData` 后
  filtered universe 与 survivor universe 收敛 → `bias_gap_resolved ≈ 0`，
  断言 < 0.2 通过.

**实现入口**：

- `kss/data/suspension_data.py`
- `kss/backtest/cost_model.py::ExecutionModel`（`__init__` 新增 kwargs、
  `filter_tradable` 追加 `_apply_suspension_filters`、新增 `is_tradable`）
- `kss/tests/test_suspension_data.py` + `kss/tests/test_execution.py::TestSuspensionFilter`/`TestIsTradable`

**后续余项**（非阻塞，留作 P1）：

- `BacktestEngine.report_universe_health()`：每日 universe 大小 + 退市 / 上市净流入；
- 可选 `delisted_return` 参数：退市股按 -90% 计入当日收益（保守反事实）.

## 其他建议

- 把 `test_adversarial.py` 加入 CI 必跑，未来若 xfail 转 pass = 工具升级；
  若新增 pass 变 fail = 回归（如有人改了 DSR 公式）。
- 可周期性扩充：添加"行业暴露泄漏"、"高换手 vs 低成本错位"等场景。
