---
type: fix
origin: docs/ideation/2026-06-21-backtest-loop-ideation.html
status: requirements
created: 2026-06-21
module: kss/backtest
tags: [lookahead-bias, walk-forward, adversarial-testing, ci, data-integrity]
---

# Feature-level Look-ahead Guard + Adversarial CI（信任地基）

## Problem Frame

`BacktestEngine.walk_forward` 的 `purge_gap` 只剔训练窗口尾部 N 天的 `future_return_Nd` 标签。
当调用方把含未来信息的列（如 `next_day_return`、`future_return_5d` 的任何移位衍生）塞进
`feature_cols` 时，test 窗口仍然拿到未来值，模型在 test 上作弊：
seed 0/1/2 全部 sharpe ≥ 5（`test_adversarial.py::test_lookahead_factor_caught_by_purge_gap`，当前 xfail）。

现状确认：
- `engine.py:273 walk_forward` 接收 `feature_cols: list[str]`，入口无任何特征合法性校验。
- `known_bias_gaps.md` Gap #1 状态 **open**，Gap #2（幸存者偏差）已 RESOLVED。
- `test_adversarial.py` 不在 `pyproject.toml [tool.pytest.ini_options] addopts` 必跑路径，仅靠手动触发。

## Actors

| Actor | 角色 |
|---|---|
| 回测使用者 | 调用 `walk_forward`，可能无意将衍生未来列放入 `feature_cols` |
| `BacktestEngine.walk_forward` | 需在入口执行特征校验，阻断泄漏特征 |
| `FeatureLookaheadGuard`（新模块） | 封装 IC 计算与豁免名单逻辑 |
| 本地 CI 脚本 / pytest 配置 | 保证 `test_adversarial.py` 每次全套测试必跑 |
| 开发者 | 维护豁免名单，处理人工复核流程 |

## Key Flows

### Flow A：walk_forward 入口校验（主路径）

1. 调用方传入 `feature_cols` + `factor_df`。
2. `walk_forward` 在**进入任何窗口分拆前**，对整体 panel 调用 `FeatureLookaheadGuard.check()`。
3. Guard 计算每个特征列与 label_col（`future_return_Nd`）的绝对 Spearman IC。
4. IC > 阈值（默认 0.95）：
   - 特征**不在豁免名单**中 → 抛出 `LookaheadFeatureError`，携带特征名 + IC 值，拒绝进入回测。
   - 特征**在豁免名单**中 → `logger.warning` 提示"已豁免，请确认非未来信息"，继续执行。
5. IC ≤ 阈值或豁免 → 正常进入 walk-forward 循环。

### Flow B：对抗测试进 CI（保障路径）

1. `pyproject.toml addopts` 追加标记或路径，使 `test_adversarial.py` 纳入默认 `pytest` 套件。
2. `test_lookahead_factor_caught_by_purge_gap` 从 `xfail` 升级为正式 `pass`（Guard 拦截后 sharpe 不再 ≥ 5，xfail 条件不再触发）。
3. 若 Guard 被删或绕过，该测试 fail，CI 阻断。

### Flow C：豁免名单人工复核

1. 开发者在 `walk_forward` 调用处传入 `lookahead_whitelist: list[str]`。
2. Guard 记录豁免特征到 warning log，不抛异常。
3. 开发者须在代码注释或 PR 描述中说明豁免理由（不由工具强制，属流程约定）。

## Acceptance Examples

| 场景 | 输入 | 预期输出 |
|---|---|---|
| 直接 look-ahead | `feature_cols=["noise", "cheat_factor"]`，`cheat_factor == next_day_return` | `LookaheadFeatureError: cheat_factor IC=1.00 > 0.95` |
| 强短周期动量因子 IC=0.97 | `feature_cols=["mom1d"]`，`lookahead_whitelist=["mom1d"]` | `WARNING: mom1d IC=0.97 > 0.95 已豁免，请确认` + 继续回测 |
| 合法特征 IC=0.60 | `feature_cols=["rsi_14"]` | 正常进入 walk-forward，无报错 |
| 阈值边界 IC=0.950 | 任意特征 IC 恰好 0.95 | **不**拒绝（严格 `>`，等于允许通过）|
| 对抗测试套件 | `pytest kss/tests/` | `test_lookahead_factor_caught_by_purge_gap` pass（不再 xfail），其余 adversarial pass 维持 |

## Requirements

### R1：FeatureLookaheadGuard 模块

**R1.1** 新建 `kss/backtest/feature_guard.py`，实现 `FeatureLookaheadGuard` 类，提供：
```python
@staticmethod
def check(
    panel: pd.DataFrame,
    feature_cols: list[str],
    label_col: str,
    ic_threshold: float = 0.95,
    whitelist: list[str] | None = None,
) -> None
```
- 对 `feature_cols` 中每列，计算与 `label_col` 的全 panel Spearman 绝对相关（`abs`）。
- IC > `ic_threshold` 且列名不在 `whitelist`：抛出 `LookaheadFeatureError`（`ValueError` 子类），message 包含特征名与 IC 数值（保留 4 位小数）。
- IC > `ic_threshold` 且列名在 `whitelist`：`logger.warning`，不抛异常。
- IC ≤ `ic_threshold`：静默通过。

**R1.2** `LookaheadFeatureError` 继承 `ValueError`，在 `kss/backtest/feature_guard.py` 中定义，从 `kss/backtest/__init__.py` re-export。

**R1.3** IC 计算使用 `pandas.DataFrame.corr(method="spearman")` 或等价实现，计算在**去 NaN dropna** 后的 `(feature_col, label_col)` 子集上进行，避免 NaN 污染相关系数。

**R1.4** `ic_threshold` 默认值 **0.95**（严格 `>`），可由调用方覆盖（面向需要临时降低阈值的研究场景）。

### R2：walk_forward 入口集成

**R2.1** `BacktestEngine.walk_forward` 在现有签名末尾新增两个 keyword-only 参数：
```python
lookahead_ic_threshold: float = 0.95,
lookahead_whitelist: list[str] | None = None,
```

**R2.2** 在 `walk_forward` 方法体**最前部**（任何数据切分逻辑之前），调用：
```python
FeatureLookaheadGuard.check(
    panel=factor_df,
    feature_cols=feature_cols,
    label_col=label_col,
    ic_threshold=lookahead_ic_threshold,
    whitelist=lookahead_whitelist or [],
)
```

**R2.3** 调用方不传这两个参数时，行为与现有 `walk_forward` 完全一致（向后兼容）——唯一变化是泄漏特征会被检测并拒绝。

### R3：IC 阈值语义与逃生口

**R3.1** 阈值 0.95 是**拒绝线**：IC > 0.95 才报错。等于 0.95 放行（边界容错）。

**R3.2** `lookahead_whitelist` 是**人工覆盖机制**：列入名单的特征不抛异常，只 warning。
适用场景：强短周期反转/动量特征在某些 label 周期下会呈现高 IC，但本质是同期数据。
- 名单由调用方维护，Guard 不内置任何默认豁免。
- 豁免 warning 格式固定：`[LookaheadGuard] 豁免特征 {col} IC={ic:.4f} 超过阈值 {threshold}`。

**R3.3** 支持 `lookahead_ic_threshold=None`（或 `float("inf")`）完全禁用检查（研究/调试用途），此时 Guard.check 立即返回，不计算 IC。

### R4：adversarial 测试升级

**R4.1** `test_lookahead_factor_caught_by_purge_gap` 的 `pytest.xfail` 逻辑反转：Guard 生效后，传入 `cheat_factor` 应触发 `LookaheadFeatureError`，测试改为断言该异常被抛出，`xfail` 标记移除。

**R4.2** 新增 `test_lookahead_guard_whitelist_passes`：验证同一 `cheat_factor` 加入 `lookahead_whitelist` 后不抛异常（警告路径）。

**R4.3** 新增 `test_lookahead_guard_threshold_boundary`：IC 恰好等于阈值时不抛异常（边界语义正确）。

### R5：CI 纳入

**R5.1** `pyproject.toml [tool.pytest.ini_options]` 的 `addopts` 目前为 `"-m 'not dl'"`。
不需要修改 addopts——`test_adversarial.py` 无 `dl` mark，已在默认收集范围内。
**唯一行动**：确认本地运行 `pytest kss/tests/` 覆盖该文件，并在 `known_bias_gaps.md` Gap #1 状态更新为 **RESOLVED**（Guard 上线后）。

**R5.2** 若项目未来引入 GitHub Actions 或其他 CI，`test_adversarial.py` 无需额外配置即自动纳入。

## Scope Boundaries

**In scope**
- `kss/backtest/feature_guard.py` 新建
- `BacktestEngine.walk_forward` 入口两个参数追加
- `test_adversarial.py` 三处测试变更（xfail 移除 + 两个新 case）
- `known_bias_gaps.md` Gap #1 状态更新

**Out of scope**
- 特征级 IC 的时序滚动检测（全 panel 一次性计算足够，逐窗口检测复杂度过高且意义有限）
- 自动白名单推断（白名单由开发者显式传入，不做 heuristic 识别）
- `factor_cross_section_backtest` 等其他入口（后续可按需接入同一 Guard）
- 多特征联合 look-ahead 检测（单列 IC 是充分条件，联合检测留作 deferred）

## Key Decisions

| 决策 | 选择 | 理由 |
|---|---|---|
| IC 方法 | Spearman（秩相关） | 对非线性 look-ahead（如分桶未来值）也能捕获，Pearson 可能漏 |
| IC 计算粒度 | 全 panel 一次 | 入口校验，不需要逐窗；计算快，早于任何切分 |
| 阈值默认值 | 0.95 | 真实 look-ahead IC 通常 > 0.99；0.95 给合法强因子留余量，不误杀 |
| 错误类型 | `ValueError` 子类 | 与现有 `walk_forward` 参数校验风格一致；调用方可 catch 具体类 |
| whitelist 语义 | warning 不抛异常 | 保留逃生口，同时强制可见性（log 记录），不静默绕过 |
| CI 配置 | 不动 addopts | `test_adversarial.py` 无 dl mark，现有配置已覆盖；零额外成本 |

## Open Questions

### Blocking

**BQ1**：全 panel Spearman IC 在 `factor_df` 行数极大（如 200 只股 × 1000 天 = 20 万行）时耗时是否可接受？
→ 需实测：`scipy.stats.spearmanr` 在此规模约 <1s，`pandas.corr(method="spearman")` 约 2-5s。若超 5s，可改为随机采样 5000 行做 IC 估计（精度足够，检测 IC>0.95 的泄漏不需要精确值）。

**BQ2**：`label_col` 非 `future_return_Nd` 格式时（如自定义 label），Guard 应如何处理？
→ 当前 `_LABEL_PATTERN` 只解析 `future_return_Nd`。Guard 的 `label_col` 参数直接用列名计算 IC，与模式无关，应无问题——但需确认 `walk_forward` 是否会在非标准 label 下提前报错（现有行为未确认）。

### Deferred

**DQ1**：是否对 `factor_cross_section_backtest`（`cross_section.py`）同样接入 Guard？
→ 该函数不训练模型，look-ahead 影响较小；可在 Guard 稳定后低成本接入，不阻断本 fix。

**DQ2**：是否记录每次 check 结果到 `storage/` 日志文件，供事后审计？
→ 单用户本地场景，logger.warning 输出到终端已足够；持久化日志留待多人协作场景。

**DQ3**：豁免名单是否应支持正则表达式（如豁免所有 `mom_*`）？
→ 字符串精确匹配足够；正则豁免可能误豁免真正的泄漏列，延后。

## Success Criteria

1. `pytest kss/tests/test_adversarial.py` 全部通过，**0 xfail**（`test_lookahead_factor_caught_by_purge_gap` 从 xfail 升为 pass）。
2. `BacktestEngine(...).walk_forward(factor_df, feature_cols=["cheat_factor"], ...)` 抛出 `LookaheadFeatureError`，message 含 IC 数值。
3. 加入 `lookahead_whitelist=["cheat_factor"]` 后不抛异常，`logger.warning` 包含"豁免"字样。
4. 现有所有回测测试（`test_backtest.py`、`test_walk_forward_combiner.py` 等）全部继续通过——Guard 对无泄漏特征零副作用。
5. `known_bias_gaps.md` Gap #1 状态标注为 **RESOLVED**，附实现入口路径。
