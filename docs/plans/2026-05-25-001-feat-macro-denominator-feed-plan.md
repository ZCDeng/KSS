---
title: "feat: 宏观分母端 daily feed（P0 of Bolton 周期框架）"
status: completed
created: 2026-05-25
type: feat
depth: standard
---

## Summary

按 Bolton《稳中求胜：利用股市周期获利》的 `P = ΣE_t(1-A)/(1+r)^t` 估值框架，把分母端 `r = i + p + ...` 的原始数据接进 KSS。当前 KSS 信号工厂几乎只看分子端（盈利预期 / 资金情绪 / 板块热度 / RPS），分母端缺位，这是 2024H2 双重过热信号 OOS 失稳的最可能解释。本期建数据底座，不动现有 combo_scan 出货逻辑；P1（周期阶段分类器）以此为输入。

**关键约束**：
- 数据层契约（kss/data/）：失败返回 None 不抛异常
- 复用 TushareClient 的 token 解析 + 退避重试
- AkShare 仅作信用利差兜底（Tushare 无对应免费接口），按需懒加载
- 不替换 update_data_daily，独立 plist + 独立日志，cron 失败不互相牵连

---

## Problem Frame

**现状**：
- KSS 数据底座 = Tushare 拉的日线 + daily_basic + 板块资金流 + 北向 + 同花顺热点
- 分母端信号（利率、收益率曲线、信用利差、通胀、货币供应）**完全没有**
- combo_scan / scanner / sector_review 都跑在分子端 + 资金端
- OOS 验证发现 bootstrap-validated 组合在 2024H2 失稳；缺分母端是合理解释之一

**目标**：
- 每日 8:35（在 update_data_daily 8:30 之后）增量拉 5 类宏观数据并落地
- 暴露 `kss.macro.MacroSnapshot` + `compute_rate_changes` + `yield_curve_slope`，给 P1-P3 消费
- 单元测试覆盖率合格（>15 cases），mock 上游不打真实 API

**非目标**（明确不做）：
- 不写周期阶段分类器（P1）
- 不改 combo_scan / scanner 任何现有输出
- 不做历史回填 > 1 年（先打通最近窗口，回填留 P1 启动前做）
- 不做信用利差的 Tushare 自研拼接（AAA-AA 不同评级曲线 Tushare 没有免费数据，留给 AkShare）

---

## Scope Boundaries

### In-Scope

- **新模块** `kss/data/macro_client.py`：
  - `MacroClient.fetch_shibor(start, end)` —— Tushare shibor
  - `MacroClient.fetch_cn_yield_curve(start, end, curve_type)` —— Tushare yc_cb，长形式
  - `MacroClient.fetch_cn_money_supply(start_m, end_m)` —— Tushare cn_m
  - `MacroClient.fetch_cn_cpi(start_m, end_m)` —— Tushare cn_cpi
  - `MacroClient.fetch_cn_ppi(start_m, end_m)` —— Tushare cn_ppi
  - `MacroClient.fetch_credit_yield_curve_akshare()` —— AkShare bond_china_yield（兜底）
  - `pivot_yield_curve(long_df, key_terms)` —— 长→宽（yld_3m/yld_1y/yld_5y/yld_10y/yld_30y）

- **新模块** `kss/macro/`：
  - `snapshot.py` —— `MacroSnapshot` dataclass + `load_macro_snapshot()`（含部分失败降级）
  - `derived.py` —— `compute_rate_changes(panel, cols, windows)` + `yield_curve_slope(wide_yc)`

- **新脚本** `scripts/update_macro_daily.py` + `scripts/run_update_macro_daily.sh`：
  - 增量更新 `storage/macro/macro_daily.parquet`（key=trade_date, upsert 语义）
  - 增量更新 `storage/macro/macro_monthly.parquet`（key=month, upsert 语义）
  - 按日存档 `storage/macro/credit_curve/<YYYYMMDD>.csv`（AkShare 单日 snapshot）

- **新 LaunchAgent** `deploy/launchd/com.zcdeng.kss.macro_daily.plist`：每个交易日 8:35

- **单元测试** `kss/tests/test_macro_client.py`：
  - MacroClient 5 个 fetch 方法的成功/失败/降级路径
  - pivot_yield_curve 的列命名 + 缺列降级 + 空输入
  - compute_rate_changes 的窗口对齐 + 单位 + 缺列降级
  - yield_curve_slope 的计算正确性 + 缺列降级
  - load_macro_snapshot 的整体拼装 + 部分失败 + 月份跨年

### Deferred to Follow-Up Work

- **历史回填**：本期只接最近 30 天 + 6 个月，回填到 2018 年留 P1 启动前批量做
- **接入 combo_scan**：Δr_20d 阈值降级 entry 候选，留 P1 完成后做
- **接入 sector_review LLM prompt**：周期阶段 tag 注入，留 P1 完成后做
- **数据质量 dashboard**：日维度 missing 字段统计 + 报警，先用 cron log 顶着

### Out-of-Scope（非本系统职责）

- 实盘下单 / 仓位调整
- 美股 / 港股宏观数据（KSS 范围只在 A 股）
- 高频分钟数据（本框架是周/月级择时，不是日内）

---

## Implementation Plan

按顺序串行：

1. ✅ `kss/data/macro_client.py` 含 MacroClient + pivot_yield_curve
2. ✅ `kss/macro/__init__.py` + `snapshot.py` + `derived.py`
3. ✅ `kss/tests/test_macro_client.py` 17 cases，全绿
4. ✅ `scripts/update_macro_daily.py` 增量 upsert 到 parquet
5. ✅ `scripts/run_update_macro_daily.sh` wrapper + chmod +x
6. ✅ `deploy/launchd/com.zcdeng.kss.macro_daily.plist` 每日 8:35
7. ✅ 真实 Tushare smoke test：拉 20240501-20240510 通过，parquet 落盘正确

---

## Verification

- 单测：`pytest kss/tests/test_macro_client.py -x -q` 17 passed
- 集成：`python3 scripts/update_macro_daily.py --since 20240501 --end 20240510 --skip-credit`
  - daily parquet: 5 行 × 37 列（含 shibor_*_d5/d20 + yld_*_d5/d20）
  - monthly parquet: 7 行 × 14 列（M0/M1/M2/CPI/PPI 完整）
- 部署：plist 已就位，等 `launchctl load` 启用（用户决定）

---

## Risks & Mitigations

| 风险 | 缓解 |
|------|------|
| Tushare 接口限频 | 已复用 _fetch_with_retry 的指数退避，且 5 个 macro API 一天调一次远低于限额 |
| AkShare 上游接口变动 | 仅作兜底 + 懒加载，未安装/失败时 missing 字段标注，不影响主流程 |
| parquet upsert 并发冲突 | cron 每日单次串行调度，无并发可能 |
| 月度数据延迟发布 | cn_m / cn_cpi 一般月中才出上月数据，update_macro_daily 多日重复拉同一月份是 upsert 语义，无副作用 |

---

## Out-Calls

- 部署后等用户跑 `launchctl load deploy/launchd/com.zcdeng.kss.macro_daily.plist` 启用 cron
- 用户验证 Tushare 积分足够支撑 yc_cb（中债国债收益率曲线）持续调用（需 2000+ 积分）
