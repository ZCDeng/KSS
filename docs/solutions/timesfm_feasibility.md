---
title: TimesFM（Google 时序基础模型）用于 KSS 预测的可行性评估
tags: [external-comparison, forecasting, foundation-model, look-ahead-bias, prediction, timesfm]
problem_type: feasibility-assessment
module: kss/prediction
created: 2026-06-17
---

# TimesFM 用于 KSS 预测的可行性评估

## TL;DR

- **结论：不接入 TimesFM 做选股预测。** 不是因为它差（它是工程扎实的时序基础模型），而是和 KSS 的问题结构 + 反 look-ahead bias 纪律三重错配，外加一个对"诚实回测"致命的硬伤。
- 三重错配：① 它是**训练**基础模型，而 KSS 8 轮实验证伪了"训练模型在科创板弱信号截面上选股"（唯一存活的是不训练的 `log_mv` 截面先验）；② 它做**单变量时序**预测，KSS 需要的是**横截面排序**——连问题类型都不是一类；③ 股票收益率≈白噪声，没有它擅长的趋势/季节性结构。
- 致命硬伤：TimesFM **预训练语料与知识截止日期官方未披露**。基础模型无法证明没见过你的回测区间 → 在以 DSR/walk-forward 为门槛的 KSS 体系里，回测**不可信**（等同 Qlib Alpha158：97/158 因子 \|t\|≥2，DSR 矫正后 0 通过）。
- 唯一窄口：宏观/分母端序列预测（Bolton 框架输入，比个股收益率更可预测），但仍要过 PIT 关，价值存疑，属"想试可小探、别抱期望"。

## 一、TimesFM 关键事实（2026-06 实测 google-research/timesfm）

| 项 | 事实 |
|----|------|
| 架构 | Decoder-only 时序基础模型（ICML 2024） |
| 最新版 | TimesFM 2.5（2025-09），200M 参数，context 上限 16k，horizon 可达 1k |
| 许可 | Apache-2.0，权重在 HuggingFace `google/timesfm-2.5-200m-pytorch` |
| 输入 | 以**单变量时间序列**为主；协变量经 XReg 支持；**无横截面的显式支持** |
| 预测 | 点预测 + 连续分位数；支持 zero-shot 与 LoRA 微调 |
| 依赖 | torch 或 jax；权重 ~900MB-1GB |
| **预训练语料/cutoff** | **官方未披露**（README 实测确认）——评估要害 |
| 金融适用性 | README 完全未提股票/金融场景，仅注明"非 Google 官方支持产品" |

## 二、三重结构错配（KSS 自己的代码已证伪同类做法）

`kss/prediction/cross_sectional_forecast.py` 的 docstring 白纸黑字记录了教训：

> DailyForecast（训练好的 LGB `model.predict` → 单股阈值）**经 8 轮实验证伪：A 股弱信号截面上 LGB MSE 训练/排序测试目标错配，Sharpe 普遍跑负**。唯一存活的是 CrossSectionalForecast——**不训练任何模型**，直接用 `log_mv` 做截面 rank。

| 维度 | KSS 的教训 | TimesFM | 错配 |
|------|-----------|---------|------|
| 训练 vs 先验 | 训练模型在这池子上输；唯一活的是不训练的先验因子 | 重型**训练**基础模型 | 正撞枪口 |
| 横截面 vs 时序 | 活下来的 α 是**横截面排序**（股票间比） | **单变量时序**（单股预测自己的路径） | 根本不是一类问题 |
| 弱信号 | 科创板收益率≈白噪声，8 层 bias 剥完 α 极稀薄 | 强在有趋势/季节性的可预测序列 | 股票收益率没有它能抓的结构 |

## 三、致命硬伤：预训练 cutoff 未披露 → 回测无法证清白

- KSS 立身之本是"诚实剥离 look-ahead bias"——任何策略要过 `Significance.is_deployable`（DSR / walk-forward / robustness）。
- TimesFM 预训练语料和截止日期未披露，基础模型**无法证明没见过回测区间**。举证责任在使用方，而你证不出来 → 回测不可信。
- 唯一能绕开的方式是只用模型 cutoff 之后的数据做纯前向预测——但那样**没有历史可回测**，KSS 不接受没过 DSR/walk-forward 的东西。死结。

## 四、附加成本

KSS 自我定位"依赖少、规模小、cron 每天跑"。TimesFM 200M + torch/jax + ~1GB 权重 + 多分钟 CPU 加载（无 GPU），是实打实的运维重量。

## 五、实测尝试（环境受阻）

为**确认**"它在 A 股无方向性优势"，写了最小验证 [`timesfm_kc50_probe.py`](../../timesfm_kc50_probe.py)：科创50（000688.SH，834 天）走式前向零样本预测，对比朴素基线——价格 5 日 MAPE（TimesFM vs 末值持平）+ 未来 1/5 日涨跌方向准确率（vs 50% 掷硬币，二项检验）。

结果：**被环境阻断**——TimesFM 2.5 权重在本机网络下载卡在 895MB `.incomplete`，反复挂起，推理跑不了。脚本与数据均就绪，权重补全后一条命令即可出数字。

注意：实测是**锦上添花**，核心否决理由（结构错配 + PIT 无法证清白）不依赖那个 MAPE/方向准确率数字，照样成立。下载受阻本身也印证了"附加成本重"这条。

## 六、建议

- **不接入 TimesFM 做选股预测。** 理由是结构错配 + PIT 无法证清白，不是模型质量——与第 9 轮"借鉴 Qlib 三个负结果"同一种诚实：好工具用错场景就是负结果。
- 若将来想验"无方向性优势"，权重补全后跑 `timesfm_kc50_probe.py` 即可（环境问题，非方法问题）。
- 真要碰基础模型，唯一值得小探的是**宏观/分母端序列**（Bolton 框架输入），且仍须先解决 PIT 清白。

## 相关

- [`qlib_paper_comparison.md`](qlib_paper_comparison.md) — 第 9 轮"借鉴三个负结果"，同款"好工具/错场景"诚实
- [`lookahead_bias_lessons.md`](lookahead_bias_lessons.md) — 8 层 bias 与 DSR 门槛，本评估"PIT 无法证清白"的依据
