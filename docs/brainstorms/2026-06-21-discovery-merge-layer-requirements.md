---
type: feat
origin: docs/ideation/2026-06-21-backtest-loop-ideation.html
status: requirements
created: 2026-06-21
tags: [discovery, merge-layer, alpha-scoring, pipeline, multi-pipeline]
module: kss/discovery
---

# 统一发现管道合并层 + 管道级 alpha 评分

## Problem Frame

KSS 当前有四条独立发现管道，各自产出候选股票列表，互不交汇：

| 管道 | 入口 | 主要输出字段 |
|------|------|------------|
| log_mv 反向 | `scripts/paper_trade_log_mv.py` | `ts_code`, `rank_pct`, `planned_weight` |
| 北证扫描 | `scripts/scan_bj50.py` | `ts_code`, composite_score（六维加权） |
| 板块热度 | `kss/sector/hotspot_rotation.py` → `scorer.py` | 板块名 + 成分股，rank_jump_threshold=50 |
| 紫苏叶产业链 | `kss/supply_chain/scoring.py` + `kss/config/supply_chain.yaml` | `ts_code`, 0-1 评分 |

四条管道存在三个系统级缺陷：

1. **无合并层**：同一只股票可能被 log_mv + 板块热度同时命中，但 Desktop/Bridge 各自呈现，用户须手动对齐。
2. **阈值硬编码无回测支撑**：`rotation_rank_jump_threshold=50`（scorer.py DEFAULT_CONFIG）、北证六维权重（scan_bj50.py 第 46-55 行）、OOS `min_n=5`（scan_combo_signals.py 第 309 行）均为经验值，无 IC/alpha 实证。
3. **管道间无有效性排序**：紫苏叶覆盖不足（supply_chain.yaml 手工标注，北证票大量缺失）；板块热度与 log_mv 在趋势市/震荡市有效性差异大；当前权重全凭直觉，无自适应。

目标：在现有四管道上方增加一层**合并 + 加权 + 去重**的 Discovery Merge Layer，并提供管道有效性估算（IC/alpha）机制，使最终候选列表质量随时间自我校准。

---

## Actors

- **KSS 系统**（单用户本地）：四条管道各自运行，产出各自 schema 的候选列表。
- **Discovery Merge Layer**（本 feat 新增）：接收所有管道输出，做标准化 → 合并 → 加权 → 去重。
- **用户**：通过 Desktop 查看最终候选股票，不感知管道内部。
- **管道权重校准器**（可选，本 feat 仅规格层面定义接口）：读取历史命中率，输出 pipeline_weight。

---

## Key Flows

### Flow 1：每日候选列表生成

```
各管道独立运行（不变）
    ↓
各管道输出 → PipelineResult（标准化 schema）
    ↓
DiscoveryMergeLayer.merge(results: list[PipelineResult])
    ├── 按 ts_code 聚合，统计 hit_count（被几条管道命中）
    ├── 跨管道加权求和（weight = pipeline_weight × pipeline_score）
    ├── 多管道共识加权（hit_count ≥ 2 → bonus multiplier）
    └── 去重：同一 ts_code 保留最高综合分，记录命中来源列表
    ↓
CandidateList（ts_code, score, sources, hit_count, pipeline_scores）
    ↓
输出至 kss_app_bridge.py / Desktop
```

### Flow 2：管道有效性估算（离线，非实时）

```
历史命中股票 + T+N 实际收益
    ↓
per-pipeline IC = rank_IC(pipeline_score, forward_return_N)
per-pipeline alpha = 管道 top-quintile 等权收益 - 基准
    ↓
写入 storage/pipeline_alpha/{date}.json
    ↓
DiscoveryMergeLayer 读取最近 K 期均值作为 pipeline_weight
```

### Flow 3：相关性检查（前置保护）

```
合并前：计算各管道当日候选集 Jaccard 相似度
若任意两管道 Jaccard ≥ 0.6 → 警告：共识加权可能放大共同偏差
（不阻断，仅在 CandidateList 元数据中标注 correlation_warning=True）
```

---

## Acceptance Examples

**Example A：单管道命中**
- log_mv 候选：`[688008, 688009, 688017]`，板块热度候选：`[688120, 688065]`
- 合并结果：5 只股票，`hit_count=1`，无 bonus，score = pipeline_weight × pipeline_score。

**Example B：跨管道共识**
- log_mv 命中 `688017`（score=0.85），板块热度也命中 `688017`（score=0.72）
- 合并后 `688017.hit_count=2`，综合分 = 加权和 × consensus_multiplier（≥1.2）
- `sources=["log_mv", "sector_hotspot"]`，Desktop 展示双来源标签。

**Example C：紫苏叶覆盖缺失**
- `688017` 未在 supply_chain.yaml → 紫苏叶管道 score=None，不参与加权
- 合并层用 `hit_count` 分母排除 None 管道（不当 0 分处理）。

**Example D：相关性告警**
- 某日板块热度和北证扫描命中列表 Jaccard=0.65
- 合并结果 metadata 附 `correlation_warning: ["sector_hotspot", "bj50_scan"]`
- Desktop 侧边栏显示"两管道今日高度重叠，共识溢价可信度降低"。

---

## Requirements

### R1：标准化输出 schema（PipelineResult）

每条管道必须产出兼容以下 schema 的结构（可以是 dataclass 或 TypedDict）：

```python
@dataclass
class PipelineResult:
    pipeline_id: str          # "log_mv" | "bj50_scan" | "sector_hotspot" | "supply_chain"
    date: str                 # YYYYMMDD
    candidates: list[CandidateItem]  # 有序列表，score 降序

@dataclass
class CandidateItem:
    ts_code: str
    score: float              # 归一化到 [0, 1]
    raw_score: float | None   # 原始分（log_rank_pct, composite_score 等），供审计
    metadata: dict            # 管道专属字段（rank_jump, chain_role 等），不要求统一
```

适配器（Adapter）将各管道现有输出映射至 PipelineResult，不改管道内部逻辑。

### R2：合并层核心逻辑

位置：`kss/discovery/merge_layer.py`

```python
class DiscoveryMergeLayer:
    def merge(
        self,
        results: list[PipelineResult],
        pipeline_weights: dict[str, float] | None = None,  # None → 等权
        consensus_multiplier: float = 1.2,                  # 多命中溢价
        min_hit_count_for_bonus: int = 2,
    ) -> MergedCandidateList:
        ...
```

- 去重键：`ts_code`
- 加权：`final_score = sum(w_i × score_i for each pipeline i that hit) × (consensus_multiplier if hit_count >= min_hit_count_for_bonus else 1.0)`
- 分母：仅计入 score 非 None 的管道数
- 输出：按 `final_score` 降序排列

### R3：管道权重持久化

- 路径：`storage/pipeline_weights.json`
- 格式：`{"log_mv": 0.35, "bj50_scan": 0.25, "sector_hotspot": 0.25, "supply_chain": 0.15, "_updated": "20260621"}`
- 首次运行无文件 → 等权（各 0.25）
- 合并层每次读取，不在运行时修改。

### R4：管道 IC/alpha 估算脚本

- 路径：`scripts/compute_pipeline_alpha.py`
- 输入：管道历史输出（各管道已有 CSV 归档）+ cs_data 前向收益
- 输出：`storage/pipeline_alpha/{date}.json`，含每条管道的 `ic_mean`, `ic_std`, `top_quintile_alpha`, `sample_n`
- 仅计算、不自动写入 pipeline_weights.json（用户手动确认后更新）。

### R5：相关性检查

合并前计算任意两管道候选集 Jaccard 相似度；任意对 ≥ 0.6 → 在 `MergedCandidateList.warnings` 中附上管道对名称。

### R6：Bridge 集成

`kss_app_bridge.py` 新增命令 `"get-discovery-candidates"` → 触发四管道适配器 + 合并层 → 返回 `MergedCandidateList` JSON。

旧的四条管道独立命令保留，不删除（向后兼容）。

---

## Scope Boundaries

**In scope（本 feat）：**
- `kss/discovery/` 新模块：`merge_layer.py` + `adapters.py` + `__init__.py`
- `scripts/compute_pipeline_alpha.py`（只读计算，不改管道）
- `storage/pipeline_weights.json`（初始等权文件）
- Bridge 新增 `get-discovery-candidates` 命令
- Desktop 候选股票列表展示 source 标签（命中来源 badge）

**Out of scope（不在本 feat）：**
- 自动更新 pipeline_weights（需单独 feat，含用户确认流程）
- 改变任何管道内部评分逻辑（rank_jump_threshold、北证六维权重等）
- 新增管道
- 紫苏叶 supply_chain.yaml 扩充标注
- 回测框架修改（significance.py / walk_forward_combiner.py 不动）

---

## Key Decisions

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 适配器 vs. 改管道 | 适配器（外层包装） | 管道各自有独立生命周期和测试；不改内部逻辑避免回归 |
| 共识溢价机制 | 固定 multiplier（可配置） | 样本量不足以学习最优值；先用简单机制，后续用 alpha 数据校准 |
| 相关性处理 | 警告不阻断 | 单用户本地工具，用户感知优先于自动拦截 |
| pipeline_weights 更新 | 手动（用户看 alpha 报告后决定） | 紫苏叶样本小、管道相关性问题需人工判断，自动更新风险高 |
| score 归一化方式 | 各 Adapter 内独立 min-max | 各管道量纲差异大（log_mv rank_pct vs. 0-1 紫苏叶）；全局归一化需跨管道联动，引入 look-ahead |

---

## Open Questions

### Blocking

1. **板块热度管道的候选粒度**：`hotspot_rotation.py` 当前输出板块级（不直接含 `ts_code`），需确认板块 → 成分股展开逻辑由哪层负责（Adapter 内展开 vs. Bridge 侧展开），以及成分股 score 如何从板块 score 继承（等分？按权重？）。

2. **log_mv 管道的每日归档**：`paper_trade_log_mv.py` 是否有每日 CSV 存档可供 `compute_pipeline_alpha.py` 消费？若无，需先补归档脚本再做 IC 计算。

### Deferred

3. **pipeline_weights 自动更新触发条件**：何时认为 alpha 数据"足够可信"可以自动写入权重？样本量门槛、置信区间门槛待后续 feat 定义。

4. **前向收益周期**：IC 计算用 T+1、T+5 还是 T+20？各管道发现逻辑时间尺度不同（log_mv 持有约 20 日，北证扫描无明确持有期）。待 `compute_pipeline_alpha.py` 原型验证后定。

5. **北证扫描与 A 股管道候选集 universe 不重叠**：北证票不在 log_mv/紫苏叶 universe，合并时 `hit_count` 天然最多为 2（北证扫描 + 板块热度）。是否需要针对北证票调整 consensus_multiplier 门槛，待观察。

---

## Success Criteria

1. `DiscoveryMergeLayer.merge()` 对任意管道子集（含空、含 None score）不抛异常，输出 `MergedCandidateList` 含正确 `hit_count` 和 `sources`。
2. Bridge `get-discovery-candidates` 命令从四管道各取今日输出，30 秒内返回合并结果（本地，无网络）。
3. 同一 `ts_code` 在合并结果中仅出现一次。
4. `compute_pipeline_alpha.py` 对至少两条有历史归档的管道输出 IC 和 top-quintile alpha，含 `sample_n` 字段以供判断可信度。
5. 相关性检查：Jaccard ≥ 0.6 的管道对在 `warnings` 中被正确标注。
6. 旧管道独立命令行为不变（回归无破坏）。
