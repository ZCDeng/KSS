---
name: investment-weekly-analysis
category: investment-research
version: 1.0.0
source: kss-bundled
protected: true
required_tools: [research_bundle]
allowed_profiles: [investment-weekly-v3]
description: 以受控分析师语料、已验证精判卡和可复算公式生成投资分析周报，并保留主题演变与分析师画像审计。
---

# 投资分析周报

本 Skill 约束周报研究步骤，不是证据源，不执行脚本，也不直接产生正式数字。

## 使用前提

- 输入必须来自用户明确选择、通过 `analyst-corpus-v1` 校验的文件；
- 每条来源保存不可变哈希、发布时间、分析师和 provenance；
- 只有通过 `precision-card-v1` checker 的卡片可以进入正式指标；
- 卖方转发只能进入背景区，不能进入共识、温度或分析师画像。

缺少真实语料、来源覆盖不足、引用无法回指或使用 synthetic fixture 时，正式完成门必须失败。

## 周报步骤

1. 冻结交易日历、统一 `as_of`、模型版本、语料与配置哈希；
2. 计算每日与全周市场温度、主题强度、风险严重度和催化剂状态；
3. 识别同向至少三个交易日、至少两名分析师覆盖的持续主题；
4. 使用 snapshot hash 固定种子的 bootstrap 区间判断升温、降温或稳定；
5. 汇总卡片数、方向分布、主题覆盖和证据等级，形成分析师画像；
6. 展示未知参数、KSS 等价公式与样例可证明公式的边界；
7. 由 Delivery Compiler 绑定 Metric Ledger、证据清单和审计附录。

输出只解释证据与历史判断，不给出买入、卖出、仓位、目标价或个性化交易行动。
