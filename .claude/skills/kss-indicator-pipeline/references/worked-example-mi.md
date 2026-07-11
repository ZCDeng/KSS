# Worked example：MI 动量（本仓库已落地）

## 会话流水线回顾（压缩）

1. **深度研究** MI：`A = C_t - C_{t-N}`，`MI = SMA(A,N)` / ewm；T+1 开盘执行。  
2. **KSS 回测** kcb50 → 自选 688017 / 688322；对照 buy&hold。  
3. **门禁** 中等边际、可解释；GO 但形态钉死、参数 walk-forward。  
4. **入库** `mi_signal` / `mi_walk_forward` / `mi_pack`；`storage/mi_rules.yaml`。  
5. **Pack** `storage/mi_signals/latest/{ts_code}.json`；`to_mi_signal` / `to_mi_overlay` / `format_mi_section`。  
6. **图** ChartWebView + chart.html；买卖箭头、MI 副图；Swift `MiChartBanner` 图外。  
7. **复盘** daily_review 注入 md；`StockReviewCard` + miSignal。  
8. **Cron** `mi_signal_pack` 工作日 17:15；日志 `storage/logs/cron/mi_signal_pack.log`。

## 关键路径

| 用途 | 路径 |
|------|------|
| 计划 | `docs/plans/2026-07-11-007-feat-mi-walkforward-signal-pack-plan.md` |
| 规则 | `storage/mi_rules.yaml` |
| Pack | `storage/mi_signals/latest/*.json` |
| 库 | `kss/strategies/mi_signal.py`, `mi_pack.py`, `kss/backtest/mi_walk_forward.py` |
| 日终 | `scripts/run_mi_signal_pack.py`, `run_mi_signal_pack_daily.sh` |
| Bridge | `scripts/kss_app_bridge.py` → `stock_detail` 的 `miSignal` / `miOverlay` |
| 图 | `Sources/KSSDesktop/Resources/chart.html`, `ChartWebView.swift`, `StockBrowserView.swift` |
| 打包 | `script/sign_and_build.sh`（勿对 Resources `chmod a-w`；勿 `swift build --show-bin-path` 二次触发 mkdir 冲突） |

## 新指标对照改名

把上表 `mi` → `<indicator_id>`，保持 **Pack 为唯一真源** 与 **STATE_ROOT** 纪律即可。
