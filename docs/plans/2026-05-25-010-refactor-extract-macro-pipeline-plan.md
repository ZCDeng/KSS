---
title: "refactor: 提取 build_indicator_panel 等共享逻辑到 kss/macro/pipeline.py"
status: pending
created: 2026-05-25
type: refactor
depth: light
---

## Summary

ce-maintainability + ce-kieran-python + ce-agent-native 三个 reviewer 都标出：
`scripts/update_macro_daily.py::refresh_regime` 用 `from scripts.backfill_regime_history
import build_indicator_panel, ensure_*` 跨脚本 import 业务逻辑，依赖 sys.path
mutation。本 plan：把共享函数提到 `kss/macro/pipeline.py`，两个脚本都从那里 import.
顺便修 #17 (`_modulate_entry_count` 双份) + #19 (`_lookup_*` 应在 kss.macro).

---

## Problem Frame

**现状**：
- `build_indicator_panel`, `ensure_pmi_vai`, `ensure_margin`, `ensure_hsgt`,
  `_normalize_hsgt`, `_normalize_margin`, `_ffill_month_value`, `_atomic_to_parquet`
  全在 `scripts/backfill_regime_history.py`
- `update_macro_daily.refresh_regime` 跨 sys.path import 它们
- Rename / 移动 / 测试都不安全
- 8 个 path 常量（DAILY_PARQUET 等）在两个脚本里重复定义

**目标**：
- `kss/macro/pipeline.py` 持有所有共享 panel-assembly + cache 逻辑
- `kss/config/paths.py` 持有所有 storage path 常量
- 两个 script 文件只剩 CLI 解析 + 调用
- 所有 import 都走 `from kss.macro.pipeline import ...` 标准路径

**非目标**：
- 不动算法（只搬代码 + 改 import）
- 不改 yaml 配置

---

## Scope Boundaries

### In-Scope

- **新文件** `kss/config/paths.py`:
  - 8 个 storage path 常量 (DAILY_PARQUET / MONTHLY_PARQUET / PMI_PARQUET /
    VAI_PARQUET / MARGIN_PARQUET / HSGT_PARQUET / REGIME_PARQUET / VALUATION_PARQUET /
    HS300_PE_PARQUET / FINA_CACHE / STOCK_BASIC_CACHE)

- **新文件** `kss/macro/pipeline.py`:
  - `_atomic_to_parquet` (从 backfill_regime_history 提)
  - `ensure_pmi_vai`, `ensure_margin`, `ensure_hsgt` (从 backfill_regime_history)
  - `build_indicator_panel`, `_normalize_hsgt`, `_normalize_margin`,
    `_ffill_month_value`, `_assemble_e_monthly`, `_first_present_col`
  - 公开 API 加 `__all__`

- **新文件** `kss/macro/queries.py` (#19 修):
  - `lookup_regime_for_date(trade_date) -> RegimeInfo | None`
  - `lookup_valuation_for_date(trade_date) -> ValuationInfo | None`
  - `lookup_rotation_hint(stage) -> RotationHint | None`
  - dataclass 替代 dict 返回，加 stale 标志（已在 scan_combo_signals 实现，提到这里）

- **修改 `scripts/backfill_regime_history.py`**:
  - 删除已提走的函数，全部改为 `from kss.macro.pipeline import ...`
  - 只保留 `parse_args` + `main`

- **修改 `scripts/update_macro_daily.py`**:
  - 删除 `from scripts.backfill_regime_history import` 跨脚本 import
  - 全改 `from kss.macro.pipeline import ...`
  - 删除重复的 8 个 path 常量，import from `kss.config.paths`

- **修改 `scan_combo_signals.py`**:
  - 删除 `_lookup_regime` / `_lookup_valuation` / `_lookup_rotation`
  - 改为 `from kss.macro.queries import ...`
  - 保留 `_modulate_entry_count` / `_apply_risk_prefilters`（scan 专属，不通用）

- **新增** `kss/macro/regime.py::modulate_entry_count_by_stage` (#17):
  把 `scan_combo_signals._modulate_entry_count` 提到 regime 模块（与 valuation 的同名兄弟对称）
  scan_combo_signals 改用 `from kss.macro.regime import modulate_entry_count_by_stage`

- **测试**：所有现有测试不动应仍 pass；新增 `kss/tests/test_macro_queries.py` 12+ cases

### Deferred

- 重命名 `_DEFAULT_CONFIG` vs yaml 的 drift 问题（#18）
- agent-native CLI sys.argv 解耦（#35 → 单独 plan）

### Out-of-Scope

- 切换 parquet 后端（datasette SQLite 双轨同步）
- 全市场 industry_map 数据补全（plan 007）

---

## Implementation Plan

1. 写 `kss/config/paths.py`
2. 写 `kss/macro/pipeline.py` — 复制（不 cut）函数 + 加 `__all__`
3. 跑 `pytest kss/tests/` 确认 import side-effect 无破坏
4. 写 `kss/macro/queries.py` + 测试
5. 修改 scripts/backfill_regime_history.py，删被提走的函数，import 改路径
6. 修改 scripts/update_macro_daily.py 类似
7. 修改 scan_combo_signals.py，`_lookup_*` 换 `from kss.macro.queries`
8. 跑全测 + smoke-test scan_combo_signals 实跑

---

## Verification

- 727+ 测试全绿（其中 ≥ 12 新 queries 测试）
- `python3 -c "from kss.macro.pipeline import build_indicator_panel"` 直接成功（无 sys.path）
- scan_combo_signals 输出 banner 与重构前完全一致

---

## Risks & Mitigations

| 风险 | 缓解 |
|------|------|
| 移动函数导致循环 import | pipeline 只 import data/macro_client，不 import scan 类业务 |
| 路径常量循环依赖 | paths.py 不 import 任何 kss/ 内东西，零依赖叶子模块 |
| scripts/ 改了之后 launchd 还跑旧版 | git push 后 launchctl kickstart 重启，验证 log 含新路径 |
