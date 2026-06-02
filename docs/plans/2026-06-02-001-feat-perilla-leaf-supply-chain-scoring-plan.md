# Plan: 紫苏叶产业链卡位评分 — 融合 Serenity 方法论

> **日期**: 2026-06-02
> **状态**: DONE (Phase 1 + Phase 2)
> **依赖**: Bolton 周期框架 (P0-P4)、themes_15th_5y.yaml、combo_scan

---

## 1. 问题陈述

KSS 当前唯一通过门槛的 alpha 是 `log_mv` 反向（小市值溢价，Sharpe 1.74）。
该策略在**统计维度**筛出小市值票，但不区分"为什么这只小市值比那只更值得持有"。

Serenity 的「紫苏叶理论」提供了一个**结构性**筛选框架：
沿产业链从终端需求逐层下钻，找到满足三个条件的"卡脖子"公司——
**不可替代 + 全球供应商 ≤2 + 尚未被市场定价**。

两者天然互补：

| 维度 | KSS log_mv | 紫苏叶理论 | 交集 |
|------|-----------|-----------|------|
| 选股逻辑 | 市值越小 alpha 越高 | 产业链越深、垄断越强 | 第5-6层卡脖子公司恰好是小市值 |
| 信息来源 | 量价+财务因子 | 供应链结构/专利/产能 | 互不重叠 = 真正的信息增量 |
| 风险控制 | 统计 DSR + 宏观 regime | 产业链需求锁定度 | combo：统计+结构双重确认 |
| 弱点 | 不知道为什么这只比那只好 | 无量化验证、无择时 | 互相补位 |

## 2. 设计目标

**不是新策略**，是给 combo_scan 现有选股叠加一层"产业链卡位"结构分数，
类似 Bolton 周期框架给 combo_scan 叠加了 rotation_score。

具体产出：
1. `kss/config/supply_chain.yaml` — 产业链元数据（每股的链层、竞争格局、需求链）
2. `kss/supply_chain/scoring.py` — 紫苏叶评分计算
3. `kss/supply_chain/registry.py` — 产业链注册/查询 API
4. 集成到 `scan_combo_signals.py` 的 `top_n_picks()` 排序键
5. Telegram banner 显示产业链卡位标签

## 3. 紫苏叶方法论提炼（6 步）

从推文中提炼出可操作化的步骤：

### 步骤 A：产业链下钻（Chain Drill-Down）
从顶层需求开始逐层追问：
```
终端需求 → 系统集成 → 核心部件 → 关键材料/设备 → 原材料/衬底
(Layer 1)   (Layer 2)   (Layer 3)   (Layer 4)        (Layer 5-6)
```
每层问：**这层要工作，下一层什么东西不可替代？**

### 步骤 B：竞争格局判定（Competitive Moat）
数该环节全球玩家数：
- **1 家（垄断）** → 最强定价权 → 紫苏叶评分最高
- **2 家（寡头）** → 有定价权 → 高分
- **3 家以上** → pass，竞争充分

### 步骤 C：需求锁定度（Demand Lock-in）
检查：上游扩产是否跟不上下游需求增长？
- 扩产周期 > 1 年 → 短期供需缺口锁定
- 技术路线唯一（无替代路径）→ 需求 100% 锁定
- 多条技术路线并存 → 需求可能分流，降分

### 步骤 D：定价偏差识别（Mispricing Detection）
Serenity 识别未被定价的信号：
- 分析师覆盖数极低（< 5 家）
- 市值 < 行业龙头的 1/10
- 市场主题叙事停留在第 1-2 层，未穿透到深层

### 步骤 E：公开检验（Adversarial Validation）
> "ChatGPT 不会反驳你。你得把东西给真人。"

在 KSS 框架下等价于：
- 回测验证（不能只靠故事）
- 与 combo_scan 信号交叉验证
- 宏观 regime 是否支持该需求链

### 步骤 F：持续监控（Chain Monitoring）
- 需求链上游是否出现新进入者？
- 技术路线是否被替代？
- 扩产周期是否缩短？
→ 任何一项变化 → 重新评估

## 4. 数据模型

### 4.1 supply_chain.yaml 结构

```yaml
version: 1
updated: "2026-06-02"

# 需求链定义（7 条主链 = themes_15th_5y.yaml 映射）
demand_chains:
  AI算力:
    layers:
      - {depth: 1, desc: "云厂商/AI应用", examples: ["MSFT", "GOOG"]}
      - {depth: 2, desc: "GPU/加速芯片", examples: ["NVDA"]}
      - {depth: 3, desc: "先进封装/光模块", examples: ["中际旭创"]}
      - {depth: 4, desc: "激光器/光芯片", examples: ["$SIVE"]}
      - {depth: 5, desc: "衬底材料/磷化铟", examples: ["$AXTI"]}
  # ... 其他需求链

# 个股产业链标注
stocks:
  688008.SH:  # 澜起科技
    name: 澜起科技
    demand_chains: [AI算力]
    chain_layer: 3           # 第3层：内存接口芯片
    chain_role: component    # 核心部件
    n_competitors_global: 3  # Montage/IDT/Rambus
    n_competitors_domestic: 1  # 国内独家
    substitutability: low    # DDR5 接口芯片无替代
    expansion_cycle_years: 2 # 扩产周期
    demand_locked: true      # DDR5 升级周期锁定
    analyst_notes: "DDR5 接口芯片国内唯一，全球三家"

  688012.SH:  # 中微公司
    name: 中微公司
    demand_chains: [半导体]
    chain_layer: 4           # 第4层：半导体设备
    chain_role: equipment
    n_competitors_global: 3  # Lam/TEL/中微
    n_competitors_domestic: 1
    substitutability: medium
    expansion_cycle_years: 3
    demand_locked: true
    analyst_notes: "刻蚀设备国产替代，5nm 验证通过"

  688981.SH:  # 中芯国际
    name: 中芯国际
    demand_chains: [半导体]
    chain_layer: 2           # 第2层：晶圆代工
    chain_role: assembler
    n_competitors_global: 5  # TSMC/Samsung/GF/UMC/SMIC
    n_competitors_domestic: 1
    substitutability: high   # 代工可切换
    expansion_cycle_years: 3
    demand_locked: false     # 受制裁影响，需求不确定
    analyst_notes: "先进制程受限，成熟制程量产"
```

### 4.2 评分公式

```
perilla_score = w_layer × layer_score
              + w_moat  × moat_score
              + w_lock  × lock_score
              + w_cover × coverage_gap_score
```

各分项（0-1 归一化）：

| 分项 | 计算 | 权重 | 来源 |
|------|------|------|------|
| **layer_score** | `(chain_layer - 1) / 5` | 0.25 | YAML 标注 |
| **moat_score** | 1 家→1.0, 2 家→0.7, 3 家→0.3, 4+→0 | 0.35 | YAML: n_competitors_global |
| **lock_score** | demand_locked × (expansion_cycle / 3) | 0.25 | YAML 标注 |
| **coverage_gap** | `1 - min(analyst_count / 20, 1)` | 0.15 | Tushare forecast 或手动 |

权重走 YAML 配置（`kss/config/supply_chain.yaml` 的 `scoring_weights` 节），
调权不改代码，与 `sector_review_config.json` 同一模式。

**评分示例**：

| 股票 | layer | moat | lock | cover | **perilla** |
|------|-------|------|------|-------|-------------|
| 688008 澜起 | 0.40 | 0.30 | 0.50 | 0.80 | **0.46** |
| 688012 中微 | 0.60 | 0.30 | 0.83 | 0.60 | **0.55** |
| 688981 中芯 | 0.20 | 0.00 | 0.00 | 0.20 | **0.10** |

中微（设备卡脖子、扩产慢）> 澜起（部件层、全球 3 家）>> 中芯（代工层、竞争充分）。
这正是紫苏叶理论的筛选顺序。

## 5. 集成到 combo_scan

### 5.1 `top_n_picks()` 排序键扩展

当前排序：`has_check → rotation_score → n_combo → mv`

新增后：`has_check → rotation_score → **perilla_score** → n_combo → mv`

```python
# scan_combo_signals.py — top_n_picks() 内
if chain_registry:
    agg['perilla_score'] = agg['sym'].apply(
        lambda s: chain_registry.score(s) * 0.3  # 权重 0.3，可配置
    )
    sort_keys.insert(2, 'perilla_score')  # rotation 之后、n_combo 之前
```

### 5.2 Banner 显示

```
[*] 风险过滤后剩 76 只 (剔除 24 只: leverage=13, liquidity=11)
  宏观阶段: I (置信度 0.76, as_of 20260522)
  产业链覆盖: 23/76 只已标注, 紫苏叶候选 8 只 (layer≥4 + moat≥0.7)
  HS300 估值: PE=14.49 n=0.91 [normal], 5Y 分位 88%
```

### 5.3 Telegram 消息增强

每只入选股附产业链标签：
```
688012 中微公司  ¥78.50 +3.2%  科创50 ⬆
  🍃 紫苏叶 0.55 | L4 设备 | 全球3家国内独家 | 需求锁定
  组合: rps50_80 + vol_break (✓科创)
```

### 5.4 LLM 板块复盘注入

`sector/commentary.py` 的 prompt 新增段：
```
## 产业链卡位分析
以下个股位于需求链深层（layer ≥ 4），且全球竞争者 ≤ 2：
- 688012 中微公司：刻蚀设备，layer 4，国内独家，半导体需求链
请在复盘中简要点评其产业链地位变化（如有）。
```

## 6. 与现有模块的关系

```
                    ┌─────────────────────────────┐
                    │   scan_combo_signals.py      │
                    │   (顶层入口)                   │
                    └───────┬─────────────────────┘
                            │
            ┌───────────────┼───────────────────────┐
            │               │                       │
     ┌──────▼──────┐ ┌──────▼──────┐ ┌──────────────▼──────────┐
     │ Bolton 周期  │ │ 风险过滤    │ │ 紫苏叶产业链 (NEW)       │
     │ P0-P4       │ │ risk_filter │ │ supply_chain/            │
     │             │ │             │ │   scoring.py             │
     │ regime      │ │ leverage    │ │   registry.py            │
     │ rotation    │ │ liquidity   │ │   config/supply_chain.yaml│
     │ valuation   │ │ st_risk     │ │                          │
     └─────────────┘ └─────────────┘ └──────────────────────────┘
            │               │                       │
            └───────────────┼───────────────────────┘
                            │
                    ┌───────▼───────┐
                    │ top_n_picks   │
                    │ 排序:         │
                    │ ✓/△ verdict  │
                    │ rotation_score│
                    │ perilla_score │ ← NEW
                    │ n_combo       │
                    │ mv            │
                    └───────────────┘
```

## 7. 实施约束

1. **不是新策略**：紫苏叶评分不改变 `is_deployable` 的统计检验流程，
   它是一个 overlay，类似 Bolton rotation_score。
2. **YAML 驱动**：所有标注数据在 YAML 里，不硬编码。
   初始覆盖科创板 51 只样本池中的头部票，逐步扩展。
3. **优雅降级**：`supply_chain.yaml` 缺失或个股未标注时，
   `perilla_score` 返回 0（中性），不影响现有排序。
4. **手工标注 + LLM 辅助**：产业链数据本质是领域知识，
   不能自动化生成。但可以用 LLM 辅助研究 + 人工审核。
5. **权重走配置**：`perilla_score` 在 `top_n_picks` 中的权重可配置，
   初始 0.3，与 rotation_score 同级。

## 8. 实施分期

### Phase 1：基础框架（本次）
- [x] 设计文档（本文件）
- [ ] `kss/config/supply_chain.yaml` — 数据结构 + 10 只科创板样例标注
- [ ] `kss/supply_chain/__init__.py` + `registry.py` + `scoring.py`
- [ ] 单元测试 `kss/tests/test_supply_chain.py`
- [ ] `scan_combo_signals.py` 集成（`top_n_picks` + banner）

### Phase 2：数据扩展（后续）
- [ ] 科创板 51 只全部标注
- [ ] 接 Tushare `report_rc` 计算 analyst_count
- [ ] Telegram 消息增强
- [ ] LLM commentary 注入

### Phase 3：动态监控（远期）
- [ ] 产业链变动检测（新进入者、技术路线替代）
- [ ] 与 combo_scan 的 OOS 验证集成
- [ ] perilla_score 因子化 → 走 significance 统计检验

## 9. 风险与已知局限

1. **标注主观性**：chain_layer / n_competitors 依赖人工判断，不同研究者可能给出不同值。
   缓解：每条标注附 `analyst_notes` 说明理由。
2. **静态快照**：产业链格局会变，但 YAML 是静态的。
   缓解：`updated` 字段 + Phase 3 动态监控。
3. **A 股特殊性**：Serenity 的案例都是美股/瑞典股，A 股科创板的定价效率可能不同。
   缓解：这正是 KSS 的强项——用统计方法验证结构性判断是否真的带来 alpha。
4. **覆盖率**：初始只标注 ~10 只，大部分股票 perilla_score = 0。
   缓解：Phase 1 用作概念验证，不期望立即改变选股结果。
