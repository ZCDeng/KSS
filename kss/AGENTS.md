# KSS Agent Guide

本文件为 AI 编码助手提供项目特定约定与扩展指南。

---

## 项目约定

### 语言与注释

- **Docstrings**: 使用中文撰写模块、类、方法的文档字符串，遵循 Google Style 格式。
- **代码注释**: 复杂逻辑使用中文行内注释；简单逻辑无需注释。
- **日志/输出**: 面向用户的字符串使用中文；内部错误信息可用英文。

### 类型提示

- 所有公共 API 必须带类型提示（函数参数与返回值）。
- 使用 `from __future__ import annotations` 启用延迟注解评估。
- 优先使用标准库类型：
  - `list[str]` 而非 `List[str]`
  - `dict[str, Any]` 而非 `Dict[str, Any]`
- 可选参数类型：`str | None`（Python 3.10+ 联合语法）。

### 导入顺序

```python
from __future__ import annotations

# 标准库
import os
from typing import Any

# 第三方库
import pandas as pd
import numpy as np

# 项目内部（绝对导入）
from kss.models.base import ModelBase
```

### 错误处理

- 数据层（`data/`）使用 `try/except` 捕获异常并记录 warning，返回 `None` 而非抛出。
- 业务层（`prediction/`、`strategies/`）在输入不合法时主动抛出 `ValueError` / `KeyError`，附带清晰中文错误信息。
- 避免裸 `except:`，至少使用 `except Exception:` 并记录日志。

---

## 如何添加新因子

1. **选择模块**: 根据因子类型归入 `features/technical.py`、`volatility.py`、`volume.py` 或 `valuation.py`。
2. **实现静态方法**: 在对应类中添加 `@staticmethod` 方法，返回 `dict[str, pd.Series]`。
3. **接入管道**: 在 `FactorPipeline.generate()` 中调用新方法并 `factors.update(...)`。
4. **添加测试**: 在 `tests/test_features.py` 中验证计算正确性。
5. **更新文档**: 在 `README.md` 的因子列表中补充说明。

### 示例

```python
# features/technical.py
@staticmethod
def my_factor(close: pd.Series) -> dict[str, pd.Series]:
    """我的自定义因子."""
    return {"my_factor": close.rolling(10).mean() / close}

# features/pipeline.py —— generate() 方法中
factors.update(TechnicalFactors.my_factor(c))
```

---

## 如何添加新策略

1. **继承基类**: 创建 `strategies/my_strategy.py`，继承 `StrategyBase`。
2. **实现接口**: 必须实现 `generate_signals()` 与 `backtest()`。
3. **注册导出**: 在 `strategies/__init__.py` 中导入并加入 `__all__`。
4. **添加测试**: 编写单元测试验证信号生成逻辑。

### 示例

```python
# strategies/my_strategy.py
from kss.strategies.base import StrategyBase
from kss.models.base import ModelBase
import pandas as pd

class MyStrategy(StrategyBase):
    def generate_signals(
        self, factor_df, feature_cols, model, **kwargs
    ) -> pd.DataFrame:
        ...

    def backtest(
        self, factor_df, feature_cols, model, **kwargs
    ) -> pd.DataFrame:
        ...
```

---

## 如何添加新通知器

1. **继承基类**: 创建 `notifications/my_notifier.py`，继承 `BaseNotifier`。
2. **实现接口**: 必须实现 `send()` 与 `is_available()`。
3. **设置 `name` 类属性**: 用于配置文件中按名称引用。
4. **注册导出**: 在 `notifications/__init__.py` 中导入（若存在）。

### 示例

```python
# notifications/my_notifier.py
from kss.notifications.base import BaseNotifier

class MyNotifier(BaseNotifier):
    name = "my_notifier"

    def send(self, title, message, level="info", **kwargs) -> bool:
        # 实现推送逻辑
        return True

    def is_available(self) -> bool:
        # 检查配置/网络是否正常
        return True
```

---

## 知识库

`docs/solutions/` — 项目历史经验沉淀（bug 复盘、运维约定、最佳实践、外部论文对照、bias 防御纪律），扁平组织，每篇带 YAML frontmatter (`module` / `tags` / `problem_type`). 动既有模块、复现旧问题或定方法论前，可按 frontmatter 字段搜索；解决新问题后用 `/ce-compound` 沉淀回此目录。

---

## 测试指南

### 运行测试

```bash
pytest tests/ -v
pytest tests/ -v --cov=kss --cov-report=term-missing
```

### 测试规范

- 每个模块对应一个测试文件：`tests/test_<module>.py`。
- 使用 `pytest` 的 `tmp_path` / `tmp_path_factory` / `monkeypatch` 等 fixture，避免污染真实文件系统或环境变量。
- 数据层测试使用 `tempfile.TemporaryDirectory()` 构造隔离的缓存目录。
- 模型相关测试使用 dummy 数据，**不依赖**外部 API 或预训练模型文件。
- 断言消息应具体：使用 `pytest.approx` 比较浮点数，使用 `match=` 参数验证异常信息。

### 测试数据构造

在测试文件中提供 `_make_df()` / `_make_factor_df()` 等辅助方法，
确保测试数据满足被测函数的列名与格式要求，避免重复构造。

---

## 文件模板

### 新模块模板

```python
"""模块一句话描述."""

from __future__ import annotations

from typing import Any

import pandas as pd


class MyClass:
    """类描述.

    多行详细说明。
    """

    def __init__(self, param: str) -> None:
        """初始化.

        Args:
            param: 参数说明。
        """
        self.param = param

    def do_something(self, df: pd.DataFrame) -> pd.DataFrame:
        """方法说明.

        Args:
            df: 输入 DataFrame。

        Returns:
            输出 DataFrame。

        Raises:
            ValueError: 输入不合法时抛出。
        """
        if df.empty:
            raise ValueError("df 不能为空")
        return df
```
