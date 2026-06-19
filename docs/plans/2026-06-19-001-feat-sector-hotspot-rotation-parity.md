# 板块热点轮动：上游字段验证与对照表

> **日期**: 2026-06-19
> **分支**: `feature/sector-hotspot-rotation-p0`
> **状态**: Phase 0 完成
> **数据来源**: `duanxianxia.com`（被上游 `hssqz/plate-rotation-skill` 使用）

---

## 1. 接口可用性验证

| 接口 | 路径 | 参数 | 可用性 | 备注 |
|---|---|---|---|---|
| 板块轮动主表 | `/api/getPlateRotatData` | `from=ths\|kaipan`, `days=N` | ✅ 可用 | 返回 JSON，业务数据在 `html` 字段中 |
| Top5 排名曲线 | `/api/getPlateRotatChart` | `from=ths\|kaipan`, `days=N` | ✅ 可用 | 已结构化，无需 HTML 解析 |
| 板块龙头矩阵 | `/api/getLongByPlate` | `platecode=XXXXXX`, `days=N` | ✅ 可用 | 返回 HTML，含龙一~龙五 |
| 单板块强度时序 | `/api/getPlateDayChart` | `platecode=XXXXXX`, `days=N` | ✅ 可用 | 已结构化；未活跃板块 `legend=null` |

**调用约束**：
- 必须带 `Referer: https://duanxianxia.com/web/main` 与 `Origin: https://duanxianxia.com`。
- 方法为 `POST`，Content-Type 为 `application/x-www-form-urlencoded`。
- 无需 API key；连续调用未触发限流，但仍应串行访问。

---

## 2. 字段对照表

### 2.1 KAIPAN 持续强度（`getPlateRotatData from=kaipan`）

| 上游字段 | KSS 输出字段 | 类型 | 说明 |
|---|---|---|---|
| `html` 中 `<span class='rank'>` | `rank` | `int` | 当日强度排名 |
| `td.plate@code` | `code` | `str` | 板块代码，如 `801001` |
| `td.plate@name` | `name` | `str` | 板块名称，如 `芯片` |
| `td.plate 第一个 <span>` | `value` | `str` | 强度分，纯数字，如 `10726` |
| `value` 无 `%` | `value_type="score"` | `str` | 与 THS 涨幅区分 |
| `span style='color'` | `color` | `str` | `red` / `green` |
| 表头 `<td>YYYY-MM-DD</td>` | `date` | `list[str]` | 日期序列，newest first，已过滤非交易日 |

**等价性结论**：KAIPAN 强度分是上游原创多因子分数（上榜次数+涨速+龙头数），KSS 无等价源，必须通过 adapter 接入。

### 2.2 THS 当日爆发（`getPlateRotatData from=ths`）

| 上游字段 | KSS 输出字段 | 类型 | 说明 |
|---|---|---|---|
| `value` 以 `%` 结尾 | `value` / `value_type="pct"` | `str` | 当日板块涨幅，如 `3.17%` |

**等价性结论**：与 KSS `moneyflow_cnt_ths.pct_change` 语义等价。KSS 现有源优先，adapter 作为可切换源。

### 2.3 Top5 排名曲线（`getPlateRotatChart`）

| 上游字段 | KSS 输出字段 | 类型 | 说明 |
|---|---|---|---|
| `date` | `date` | `list[str]` | MM-DD 日期序列 |
| `legend` | `legend` | `list[str]` | 带 "(N次上榜)" 后缀的板块名 |
| `name["1".."5"]` | `top5_names` | `list[str]` | Top5 板块名 |
| `"1".."5"[].value` | `rank` | `int` | 每日排名；未上榜为 `10.5` + `wu.png` symbol |
| `"1".."5"[].symbol` | `symbol` | `str` | 排名图标 URL；`wu.png` 表示当日未上榜 |

### 2.4 板块龙头矩阵（`getLongByPlate`）

| 上游字段 | KSS 输出字段 | 类型 | 说明 |
|---|---|---|---|
| 表头日期 | `date` | `str` | 对应交易日 |
| `div.kline@code` | `code` | `str` | 股票代码 6 位 |
| `div.kline span[0]` | `rank` | `str` | `龙一` / `龙二` / ... / `龙五` |
| `div.kline span[1]` | `name` | `str` | 股票名称 |
| 跨天聚合 | `count` | `int` | 该股票近 N 日当龙头次数 |
| 跨天聚合 | `positions` | `list[str]` | 如 `2026-06-18/龙一` |

**等价性结论**：KSS 现有 `ths_hot` / `dragon_tiger` 无法按板块维度给出龙一~龙五，必须通过 adapter 接入。

### 2.5 单板块强度时序（`getPlateDayChart`）

| 上游字段 | KSS 输出字段 | 类型 | 说明 |
|---|---|---|---|
| `legend` | `legend` | `list[str]\|None` | 指标名；`None` 表示该板块近 N 日未活跃 |
| `date` | `date` | `list[str]` | 日期序列 |
| `series1`, `series2` | `series1`, `series2` | `list` | 强度和量能序列 |

---

## 3. 验证样例

KAIPAN Top5（2026-06-18）：

```json
[
  {"rank": 1, "code": "801001", "name": "芯片",     "value": "10726", "value_type": "score"},
  {"rank": 2, "code": "801159", "name": "机器人概念", "value": "10376", "value_type": "score"},
  {"rank": 3, "code": "801660", "name": "通信",     "value": "9882",  "value_type": "score"},
  {"rank": 4, "code": "801807", "name": "算力",     "value": "7907",  "value_type": "score"},
  {"rank": 5, "code": "801694", "name": "非金属材料", "value": "6791",  "value_type": "score"}
]
```

THS Top5（2026-06-18）：

```json
[
  {"rank": 1, "code": "885926", "name": "牙科医疗",   "value": "3.17%", "value_type": "pct"},
  {"rank": 2, "code": "885907", "name": "科创次新股", "value": "2.82%", "value_type": "pct"},
  {"rank": 3, "code": "885937", "name": "培育钻石",   "value": "2.81%", "value_type": "pct"},
  {"rank": 4, "code": "885343", "name": "稀土永磁",   "value": "2.49%", "value_type": "pct"},
  {"rank": 5, "code": "886102", "name": "中国AI 50",  "value": "2.37%", "value_type": "pct"}
]
```

板块 `801001` 近 5 日妖王榜：

```json
[
  {"code": "600353", "count": 3, "name": "旭光电子", "positions": ["2026-06-18/龙一", "2026-06-17/龙一", "2026-06-16/龙二"]},
  {"code": "000032", "count": 3, "name": "深桑达Ａ", "positions": ["2026-06-16/龙一", "2026-06-15/龙一", "2026-06-12/龙四"]},
  {"code": "002741", "count": 2, "name": "光华科技", "positions": ["2026-06-17/龙三", "2026-06-16/龙三"]}
]
```

---

## 4. 风险与决策

| 风险 | 影响 | 决策 |
|---|---|---|
| `duanxianxia.com` 接口失效或加鉴权 | Phase 2/3 阻塞 | 保留 KSS-only 降级路径：KAIPAN 强度分缺失时降级为 `flow_persistence_score`；妖王榜缺失时不启用 leader 信号 |
| HTML 模板漂移 | 解析失败 | 解析函数用宽松正则，失败时返回空列表并记录 warning，不抛异常 |
| 板块命名空间不一致（THS 88x vs KAIPAN 80x） | 跨源比较错误 | 分类时只在各自命名空间内排名，不直接比较 THS 涨幅与 KAIPAN 强度分 |
| `getPlateDayChart.legend=null` | 被误判为失败 | 作为"未活跃"正常处理，不降级 adapter |

---

## 5. 新增文件

- `kss/data/plate_rotation_adapter.py`：四个接口的 adapter + 解析函数。
- `scripts/sector_rotation_probe.py`：Phase 0 探针脚本，用于复验接口可用性。
- `storage/sector_rotation/probe/*.json`：原始响应与解析样例（不提交，仅本地验证）。

---

## 6. 下一阶段（Phase 1）前提

- ✅ KAIPAN 强度分可通过 adapter 获取。
- ✅ 板块龙头矩阵可通过 adapter 获取。
- ✅ 妖王榜可基于 adapter 计算。
- ✅ 接口失败路径有明确降级策略。

可以开始 Phase 1：KSS 源单日快照（`kss/sector/hotspot_rotation.py` + `scripts/refresh_hotspot_rotation.py`）。
