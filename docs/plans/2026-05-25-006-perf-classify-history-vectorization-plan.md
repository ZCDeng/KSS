---
title: "perf: classify_history O(N²) → expanding().quantile() 向量化"
status: pending
created: 2026-05-25
type: perf
depth: light
---

## Summary

把 `kss/macro/regime.py::classify_history` 的 expanding-window 循环
（2090 日 × 5 维度 × per-row quantile slice ≈ 9190 次 quantile 操作）
重写成 pandas 原生 `expanding().quantile()` 向量化版本。预期 ~100x 加速：
update_macro_daily 的 1.5h 日刷新 → 秒级.

---

## Problem Frame

**现状**：
- `classify_history` 内部 `for i in range(len(df))` 逐行展开 `df.iloc[:i]` 切片
- 每个切片调 `compute_thresholds(hist, cfg)` → 5 列 `s.quantile()`
- ce-performance-reviewer 实测：日刷新主要时间花在这里（PERF-01，confidence 100）
- 随历史延长（2030 年 ≈ 4000 日）runtime 还会 ~4x

**目标**：
- `classify_history` 输出与现实现 byte-identical（同样 stage_raw 序列）
- runtime 降到 < 5s for 2090-day panel
- 加 benchmark test 防回归

**非目标**：
- 不改 stage 规则 / 阈值 / hysteresis 算法
- 不改 `classify_today` / `classify_day`（单日 API 不动）

---

## Scope Boundaries

### In-Scope

- 改写 `classify_history`：先用 `df[col].expanding(min_periods=min_n).quantile(q_low/q_high)` 一次性算出全部阈值序列
- 然后对每行用查表式 `_direction(value, (low_th[i], high_th[i]))` 落桶
- 保留 `use_rolling=True` 分支（用 `rolling(min_n).quantile()`）
- 保留 hysteresis 处理（`_apply_hysteresis` 不动）
- 新增 `kss/tests/test_regime.py::test_classify_history_vectorized_matches_original` —
  用相同 panel 跑老/新两路，逐行 assert 一致
- 新增 `kss/tests/test_regime.py::test_classify_history_benchmark_under_5s` —
  2000-row synthetic panel，wall-clock < 5s

### Deferred

- update_macro_daily 增量模式（只算新日期）→ #refresh_regime 重构留 plan 010
- `compute_e_trend` / `compute_liquidity_index` 内部循环优化（PERF-06/07，小头）

### Out-of-Scope

- 改 `RegimeThresholds` dataclass 形状
- 切换到 numba / cython

---

## Implementation Plan

1. 给 `compute_thresholds` 加 `from_series` 入口，接受预算好的 (low_q, high_q) 元组
2. 写 `_precompute_expanding_thresholds(panel, cfg) -> dict[col, (Series, Series)]`
3. 重写 `classify_history`：调用 1 次预算阈值，循环只剩 `classify_day` 查表
4. parity 测试：随机 60 日 panel，老/新两路 stage_raw / confidence 完全相等
5. benchmark 测试 + CI 集成（gate at < 5s）

---

## Verification

- 24 个现有 regime 测试全部仍通过
- parity 测试 vs current implementation 完全一致
- benchmark 测试 < 5s
- 实跑：`time python3 -c "from scripts.update_macro_daily import refresh_regime; ..."` 验证总耗时

---

## Risks & Mitigations

| 风险 | 缓解 |
|------|------|
| pandas `expanding().quantile()` 在小窗口（< min_n）有 NaN 边界 | 用 `min_periods=min_n` 严格对齐，前 min_n 日保留 Unknown |
| use_rolling=True 分支阈值序列不同 | 单独跑两路 parity 测试 |
| 向量化后 evidence 字段不一致 | classify_day 保持原状，只是参数来源不同 |
