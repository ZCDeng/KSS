# KSS — Keda Stock System

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 科创50成分股专业分析跟踪系统  
> 基于 LightGBM/XGBoost 的量化选股与 Walk-forward 回测框架。

---

## 功能特性

- **数据层**: Tushare / AKShare 双源接入，CSV 本地缓存，自动过期检测
- **因子工程**: 49+ 技术/波动率/成交量/估值因子，截面 Z-Score 标准化
- **模型训练**: LightGBM 滚动训练，模型版本注册中心（自动清理旧版本）
- **回测引擎**: Walk-forward 纯多头回测，含换手率与交易成本建模
- **预测模块**: 日度/周度/多周期趋势预测，再平衡建议生成
- **通知系统**: 控制台 / 企业微信 / 钉钉 / 邮件多通道通知
- **CLI 工具**: 统一的 `kss` 命令行接口，支持日常自动化脚本

---

## 安装

```bash
git clone <repo-url>
cd KSS
./scripts/setup.sh
source .venv/bin/activate
```

或手动安装：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `TUSHARE_TOKEN` | Tushare Pro API Token | 是 |
| `KSS_CONFIG` | 自定义配置文件路径 | 否 |

Token 也可写入以下任一文件：
- `~/.tushare/token`
- `./.tushare_token`
- `./tushare_token.txt`

---

## 快速开始

### 1. 更新数据

```bash
kss update --pool kcb50
```

### 2. 训练模型

```bash
kss train --pool kcb50 --period 10 --output lgb_kcb50_10d
```

### 3. 执行回测

```bash
kss backtest --pool kcb50 --period 10 --plot
```

### 4. 生成预测

```bash
kss predict --pool kcb50 --model lgb_kcb50_10d --type daily
```

---

## CLI 命令参考

| 命令 | 说明 | 常用选项 |
|------|------|----------|
| `kss update` | 更新股票池行情数据 | `--pool`, `--start`, `--end` |
| `kss train` | 训练预测模型 | `--pool`, `--period`, `--output` |
| `kss backtest` | Walk-forward 回测 | `--pool`, `--period`, `--plot` |
| `kss predict` | 生成预测报告 | `--pool`, `--model`, `--type` |
| `kss scan` | 每日信号扫描 | `--pool`, `--date` |
| `kss report` | 生成格式化报告 | `--type`, `--output` |
| `kss notify` | 发送通知消息 | `--title`, `--message`, `--level` |
| `kss analyze` | 因子重要性分析 | `--model`, `--top` |

使用 `kss <command> --help` 查看各命令详细参数。

---

## 架构概览

```
kss/
├── cli/              # Click 命令行接口
├── data/             # Tushare/AKShare 客户端、CSV 缓存
├── features/         # 因子生成管道（技术/波动率/成交量/估值）
├── models/           # 模型基类、LightGBM 实现、注册中心
├── strategies/       # 策略基类、横截面选股、信号生成器
├── backtest/         # Walk-forward 引擎、绩效指标、成本模型
├── prediction/       # 日度/周度/周期预测与格式化输出
├── notifications/    # 通知通道抽象与具体实现
├── config/           # YAML 配置文件
├── scripts/          # 自动化脚本（setup.sh、daily_run.sh）
└── tests/            # pytest 测试套件
```

---

## 配置指南

主配置文件位于 `config/settings.yaml`，关键配置项：

```yaml
# 股票池
stock_pool:
  default: "kcb50"

# 交易成本
costs:
  buy: 0.001   # 买入 0.1%
  sell: 0.002  # 卖出 0.2%

# 模型参数
models:
  lightgbm:
    learning_rate: 0.05
    num_leaves: 31

# 回测参数
backtest:
  train_window: 120
  retrain_freq: 5
  top_pct: 0.2

# 预测阈值
prediction:
  thresholds:
    strong_up: 0.05
    mild_up: 0.02
    neutral: -0.02
    mild_down: -0.05
```

---

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest tests/ -v --cov=kss

# 代码格式化
black kss/ tests/
ruff check kss/ tests/

# 类型检查
mypy kss/
```

---

## 许可证

MIT License © 2024 Keda
