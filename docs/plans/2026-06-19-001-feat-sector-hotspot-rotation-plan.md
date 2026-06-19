# Plan: KSS 板块热点轮动数据能力 —— 完整移植与 KSS-native 落地

> **日期**: 2026-06-19
> **分支**: `feature/sector-rotation-data-capability`
> **状态**: ✅ 已完成 —— Phase 0-4 全部落地 main（2026-06-20 合并 `feature/sector-hotspot-rotation-p3`）；swift build + pytest 21/21 通过。唯一待积累项：`storage/sector_rotation/` 现仅归档 1 天，历史聚合指标需每日 launchd 任务跑起来逐天累积。
> **上游目标**: 完整移植 QuantDash 板块轮动 + plate-rotation-skill 双源/妖王榜能力，优先复用 KSS 现有数据源；现有源无法等价覆盖的字段必须通过 adapter 接入，禁止偷换为近似语义。

---

## 1. 目标与范围

### 1.1 必须交付的三项能力

| # | 上游项目 | 能力 | 关键字段/算法 |
|---|---|---|---|
| 1 | `rancy777/quantdash-ai-stock` | 东方财富板块涨幅排名 + 近 N 日持续性统计 | 当日 `pct_change` 排名；`top3_appearances`；`streak_days`；`strength_delta` |
| 2 | `hssqz/plate-rotation-skill` | THS 当日爆发 + KAIPAN 持续强度双源框架 | THS `pct_change%`；KAIPAN `strength_score`；双源交叉分类 |
| 3 | `hssqz/plate-rotation-skill` | 板块龙头股跨天频次 / 妖王榜 | `getLongByPlate` 返回的每日 `龙一`…`龙五`；跨天频次 `count`；`positions` |

### 1.2 设计原则

1. **KSS 现有源优先**：能用 `moneyflow_ind_dc`、`moneyflow_cnt_ths`、`sw_daily`、`ths_hot`、`dragon_tiger`、`etf_radar` 表达的能力，优先复用。
2. **不等价不伪装**：现有源无法等价表达的上游字段（KAIPAN `strength_score`、`getLongByPlate` 板块-龙头矩阵），必须通过 adapter 接入原始接口或明确列为 parity gap；禁止把 KSS 资金流持续性包装成 KAIPAN 强度分。
3. **数据层先做，UI 后做**：Phase 1 只产出 Python 模块、归档 JSON、单测；Phase 2/3/4 才碰 Swift / UI / 任务入口。

---

## 2. 上游能力映射表

| 上游能力 | 原字段 / 算法 | KSS 现有等价源 | 是否等价 | 本期处理 |
|---|---|---|---|---|
| QuantDash 行业/概念当日涨幅排名 | `pct_change` 降序 | `moneyflow_ind_dc.pct_change` / `moneyflow_cnt_ths.pct_change` | 等价 | Phase 1 |
| QuantDash 近 N 日 Top3 出现次数 | 回看历史 Top 榜计数 | 自建 `storage/sector_rotation/*.json` 归档 | 等价 | Phase 1 |
| QuantDash 连续霸榜天数 | 从当前日往前同名 leader 计数 | 同上 | 等价 | Phase 1 |
| QuantDash leader 涨幅环比 | leader 今日 vs 昨日 `pct_change` | 同上 | 等价 | Phase 1 |
| plate-rotation THS 当日爆发 | `from=ths` 涨幅 % | `moneyflow_cnt_ths.pct_change` | 等价 | Phase 1 |
| plate-rotation KAIPAN 持续强度 | `from=kaipan` 强度分（上榜次数+涨速+龙头数） | 无等价 | **不等价** | Phase 2 adapter |
| plate-rotation Top5 排名曲线 | `getPlateRotatChart` ECharts 数据 | 自建归档 + 排名时序 | 等价 | Phase 2 |
| plate-rotation 板块龙头矩阵 | `getLongByPlate` 每日龙一~龙五 | 无等价 | **不等价** | Phase 3 adapter |
| plate-rotation 妖王榜 | `rank_plate_long_persistence` | 无等价 | **不等价** | Phase 3 adapter |

**说明**：
- `KAIPAN strength_score` 与 KSS `compute_flow_persistence()` 是不同因子，不得混用。
- `getLongByPlate` 提供的是“按板块代码查询的每日领涨股矩阵”，KSS 现有 `ths_hot`/`dragon_tiger` 只给全市场强势股/龙虎榜，且没有规范板块归属字段。

---

## 3. 数据源契约

### 3.1 可用源

| 数据源 | 用途 | 关键字段 |
|---|---|---|
| `moneyflow_ind_dc` | 东财行业单日涨幅/资金流 | `name`, `pct_change`, `net_amount_rate`, `buy_elg_amount_rate` |
| `moneyflow_cnt_ths` | 同花顺概念单日涨幅/资金流 | `name`, `pct_change`, `net_amount` |
| `sw_daily` | 申万行业指数日线 | `ts_code`, `name`, `pct_change`, `amount` |
| `ths_hot` | 同花顺强势股 + 题材归因 | `code`, `name`, `pct_change`, `reason` |
| `dragon_tiger` | 东财龙虎榜明细 | `code`, `name`, `net_amount`, `reason` |
| `etf_radar` | 主题 ETF 申赎/强势确认 | `flow_5d`, `grade`, `divergence`, `past5_ret` |
| `stock_names.csv` | 股票池静态信息 | `ts_code`, `name`, `industry`, `concept` |

### 3.2 不可用于板块归属的字段

- `ths_hot.reason`：自由文本题材归因，不是板块 membership。
- `dragon_tiger.reason`：上榜原因，不是板块 membership。
- `stock_names.csv.concept`：仅覆盖 KCB 池且稀疏，不能代表全市场概念归属。

### 3.3 数据源使用纪律

- 数据层异常只记录 warning 并写入 `missing`，不抛异常（遵循 AGENTS.md）。
- 外部 HTTP 源（`ths_hot`、`dragon_tiger`、KAIPAN adapter、`getLongByPlate` adapter）串行调用，避免触发上游限流。
- 所有金额/比率字段保留原始单位，JSON 中显式标注。

---

## 4. 指标字典

所有指标必须有显式公式，禁止“显著/弱”等模糊条件。

| 指标 | 公式 / 计算规则 | 适用 source |
|---|---|---|
| `today_rank` | 按 `pct_change` 降序的 `method="min"` 排名 | industry / concept |
| `previous_rank` | 上一交易日的 `today_rank` | industry / concept |
| `rank_jump` | `previous_rank - today_rank` | industry / concept |
| `top3_appearances` | 最近 N 个交易日中进入 Top3 的次数 | industry / concept |
| `streak_days` | 从当前交易日往前，连续保持 `today_rank == 1` 的交易日数 | industry / concept |
| `strength_delta` | 当前 leader 的 `pct_change` 与前一交易日 leader `pct_change` 的差；当前 leader 变化时为 `null` | industry / concept |
| `heat_score` | 按配置权重对归一化后的 `pct_change` / 资金流列加权求和（复用 `compute_heat_score`） | industry / concept |
| `kaipan_strength_score` | 上游 KAIPAN 原始强度分 | kaipan adapter |
| `kaipan_rank` | 按 `kaipan_strength_score` 降序排名 | kaipan adapter |
| `flow_persistence_score` | `cum_inflow` + `persist_days` 综合（复用 `compute_flow_persistence`） | kss flow |
| `leader_coverage` | 有龙头映射的板块占比 | leader adapter |

---

## 5. 分类规则

分类依赖两个维度：**当日爆发** 与 **持续强度**。

### 5.1 维度定义

| 维度 | source | 判定 |
|---|---|---|
| 当日爆发 | THS / `moneyflow_cnt_ths` 涨幅排名 | `today_rank <= top_n` |
| 持续强度 | KAIPAN `strength_score` 排名 或 KSS `flow_persistence_score` | `kaipan_rank <= top_n` 或 `flow_persistence_rank <= top_n` |

### 5.2 四象限分类

| 当日爆发 | 持续强度 | 分类 | 条件 |
|---|---|---|---|
| 是 | 是 | 真主线 | `today_rank <= top_n` 且 `kaipan_rank <= top_n` 或 `top3_appearances >= 2` |
| 是 | 否 | 妖板 / 突发热点 | `today_rank <= top_n` 且 `top3_appearances <= 1` 且 `rank_jump >= 5` |
| 否 | 是 | 老热点 / 退潮观察 | `today_rank > top_n` 且 `top3_appearances >= 2` 或 `kaipan_rank <= top_n` |
| 否 | 否 | 卫星 | 其他 |

### 5.3 分类置信度

- `classificationConfidence`：`high` / `medium` / `low`。
- 当关键源缺失（如 KAIPAN adapter 失败、`ths_hot` 缺失）时，分类置信度降级。
- `leader_stocks` 只在 `leader_coverage >= 0.5` 时参与分类。

---

## 6. 输出结构

### 6.1 归档 JSON

```json
{
  "tradeDate": "20260619",
  "lookbackDays": 5,
  "tradingDaysUsed": ["20260619", "20260618", "20260617", "20260616", "20260613"],
  "historyCoverage": 1.0,
  "missing": [],
  "industries": [...],
  "concepts": [...],
  "kaipanBoards": [...],
  "leaderBoards": [...],
  "crossSourceSignals": {
    "mainline": [...],
    "demonBoard": [...],
    "oldHotspotFading": [...],
    "satellite": [...]
  }
}
```

### 6.2 单板块对象

```python
HotspotBoard:
  name: str
  source: "industry" | "concept" | "kaipan" | "leader"
  boardCode: str | None
  pctChange: float | None
  heatScore: float | None
  todayRank: int
  previousRank: int | None
  rankJump: int | None
  top3Appearances: int
  streakDays: int
  strengthDelta: float | None
  kaipanStrengthScore: int | None
  kaipanRank: int | None
  flowPersistenceScore: float | None
  classification: "mainline" | "demon" | "fading" | "satellite"
  classificationConfidence: "high" | "medium" | "low"
  evidenceSources: list[str]
  leaderStocks: list[LeaderStock] | None
  missing: list[str]

LeaderStock:
  symbol: str
  name: str
  appearances: int
  positions: list[str]   # e.g. "20260618/龙一"
  pctChange: float | None
  dragonTigerHits: int
  hotReasonHits: int
  reasons: list[str]
  lastSeenDate: str
```

---

## 7. 分阶段实施

### Phase 0 — Parity 验证（1-2 天）

产出：
- KAIPAN adapter 样例响应（确认字段、编码、Referer 要求）。
- `getLongByPlate` adapter 样例响应（确认板块代码、龙一~龙五结构）。
- 全市场 stock→board 映射覆盖率探针（可选替代方案）。

验收：
- 每个 adapter 能返回至少一个交易日的样例 JSON。
- 输出与上游参考文档字段级对照表。

### Phase 1 — KSS 源单日快照（2-3 天）

交付：
- `kss/sector/hotspot_rotation.py`
- `scripts/refresh_hotspot_rotation.py`
- `storage/sector_rotation/YYYYMMDD.json`
- 单测 + golden fixture

范围：
- 只用一个 `trade_date` 的 `moneyflow_ind_dc` / `moneyflow_cnt_ths` / `sw_daily` 生成当日排名与 `heat_score`。
- 不计算历史聚合、分类、leader persistence。
- 不碰 Swift / UI / KSSTask。

验收：
- JSON schema 与单测一致。
- dry-run 输出与 Tushare 原始字段逐行一致。

### Phase 2 — 历史聚合与双源分类（2-3 天）

交付：
- 最近 N 个交易日窗口（用 Tushare `trade_cal` 或已归档日期）。
- `top3_appearances`、`streak_days`、`rank_jump`、`strength_delta`。
- KAIPAN adapter 接入（如 Phase 0 验证通过）。
- 四象限分类。
- Top5 排名曲线。

验收：
- 节假日不破坏 streak / Top3 计数。
- `historyCoverage` 不足时分类降级。

### Phase 3 — 龙头 / 妖王榜（3-4 天）

交付：
- `getLongByPlate` adapter 或全市场 board membership 实现。
- `leaderStocks`、`appearances`、`positions`。
- 妖王榜排序。
- 分类规则在 leader coverage 达标时启用 leader 信号。

验收：
- `leader_coverage` 可观测。
- coverage 不足时，leader 信号不用于分类。

### Phase 4 — 桌面端接入（2-3 天）

交付：
- `scripts/kss_app_bridge.py` 增加 `sectorRotationHistory`。
- `Sources/KSSDesktop/Models/KSSModels.swift` 增加 Swift model。
- `ReviewsView` 日期列表 + 详情表格。
- `DashboardView` 只展示最新摘要卡片。
- `KSSTask.refreshSectorRotation`。

验收：
- Dashboard 显示最新 `真主线/妖板` 数量与 Top leader。
- ReviewsView 可切换日期查看完整榜单与妖王榜。

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| KAIPAN / `getLongByPlate` 上游接口失效或封 IP | Phase 2/3 阻塞 | 先做 Phase 1；上游不可用时保留 parity gap 并降级为 KSS-only 指标 |
| `ths_hot` / `dragon_tiger` 字段漂移 | leader 聚合错误 | 严格按现有 client 的 `_COLUMN_RENAME` 和 `_REQUIRED` 校验 |
| 板块命名空间不一致（东财/同花顺/申万） | 跨源比较错误 | 只比较同名/同 source 的排名；不做跨命名空间直接 join |
| 历史覆盖不足导致 streak 被截断 | 分类错误 | 用交易日历；`historyCoverage` 低于阈值时降级 |
| Phase 1 提前扩散到 Swift/UI | schema 返工 | 禁止 Phase 1 修改 bridge/Swift/UI/KSSTask |

---

## 9. 验收标准

### Phase 1

- [x] `pytest kss/tests/test_hotspot_rotation.py -v` 通过（21/21）。
- [x] dry-run 输出 `storage/sector_rotation/YYYYMMDD.json`（已有 `20260618.json`）。
- [x] JSON 字段与计划 6.1/6.2 一致（bridge `sector-rotation` 实跑字段对齐）。
- [x] 不修改任何 Swift / bridge / KSSTask 文件（Phase 1 提交 `61b59b3` 仅动数据层）。

### Phase 2

- [x] KAIPAN adapter 返回样例与上游字段对照（`hotspot_rotation.py` / `sector_rotation_probe.py`）。
- [x] 分类规则覆盖真主线/妖板/老热点/卫星四种情况（`_classify_board` → `mainline`/`demonBoard`/`oldHotspotFading`/`satellite`）。
- [x] 节假日 streak / Top3 计数正确（交易日历窗口，单测覆盖）。

### Phase 3

- [x] `getLongByPlate` 或 board membership 输出 `leaderStocks`。
- [x] `leader_coverage` 可观测（输出字段 `leaderCoverage` + `LEADER_COVERAGE_THRESHOLD` 门控）。
- [x] 妖王榜排序与上游语义一致（leader 跨天 `count` 持久化排序）。

### Phase 4

- [x] Dashboard 显示最新摘要（`HotspotRotationCard`）。
- [x] ReviewsView 支持日期切换与详情（「热点轮动」模式：日期列表 + 四象限 + 板块表 + 妖王榜）。
- [x] KSSTask 刷新入口可用（`KSSTask.refreshSectorRotation` + bridge `refresh-sector-rotation`）。

---

## 10. 相关文档

- `docs/plans/2026-06-15-002-feat-sector-realtime-pillar-p1-plan.md` — 实时数据接入 P1 裁决
- `docs/solutions/sector_review_deployment.md` — 板块复盘部署
- `docs/solutions/etf_flow_signal_lessons.md` — ETF 申赎信号回测教训
- `kss/sector/scorer.py` — 现有 heat/persistence/rotation 函数
- `kss/sector/data_fetcher.py` — 现有 SectorSnapshot
