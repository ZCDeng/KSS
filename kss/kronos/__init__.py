"""KSS × Kronos —— K 线基础模型的只读离线接入.

里程碑一：离线影子部署 + 合成 K 线压测。把 vendored Kronos（``kronos_vendor/``）
当只读批处理用在两处它擅长且无法被刷分的能力上：

- **影子通道**（:mod:`kss.kronos.shadow`）：前向预测走
  ``is_deployable(strategy_family="mined")`` 闸门，积累无污染前向战绩。
- **合成压测**（:mod:`kss.kronos.stress_diagnostic`）：生成对抗 regime，
  量化现有闸门在已知零结构数据上的假阳性率（诊断，非新硬闸门）。

设计铁律：

- ``log_mv`` 实盘路径全程不动；批处理与实盘**零连接**。
- 冻结截断日 ``D`` 做泄漏隔离：微调只用 D 之前，影子/压测只跑 D 之后。
- 数据层失败一律降级或 fail-loud，不静默丢失（与 :mod:`kss.data` 契约一致）。

vendored 源码经 :func:`load_vendor` 挂上 ``sys.path`` 导入，避免污染顶层
``model`` 命名空间。
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 仅类型注解
    from types import ModuleType

# kss/kronos/__init__.py → parents[0]=kronos, [1]=kss, [2]=KSS root.
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
KRONOS_VENDOR_ROOT: Path = PROJECT_ROOT / "kronos_vendor"


@lru_cache(maxsize=1)
def load_vendor() -> tuple[type, type, type]:
    """把 ``kronos_vendor/`` 挂上 ``sys.path`` 并返回核心类.

    vendored ``model/kronos.py`` 内部用 ``from model.module import *``（上游
    绝对导入，字节保持一致）；因此把 ``kronos_vendor/`` 这层目录加进 ``sys.path``，
    让 ``model`` 解析为 ``kronos_vendor/model``，而非污染为可被 pip 发现的包。

    Returns:
        ``(Kronos, KronosTokenizer, KronosPredictor)`` 三个类对象.

    Raises:
        FileNotFoundError: ``kronos_vendor/`` 缺失（未 vendoring）.
        ImportError: 隔离依赖未安装（``pip install -r kronos_vendor/requirements.txt``）.
    """
    if not KRONOS_VENDOR_ROOT.exists():
        raise FileNotFoundError(
            f"kronos_vendor 不存在: {KRONOS_VENDOR_ROOT}. "
            "先 vendoring（见 kronos_vendor/README.md）。"
        )
    vendor_str = str(KRONOS_VENDOR_ROOT)
    if vendor_str not in sys.path:
        sys.path.insert(0, vendor_str)
    try:
        from model import Kronos, KronosPredictor, KronosTokenizer  # type: ignore
    except ImportError as exc:  # pragma: no cover - 依赖缺失分支
        raise ImportError(
            "导入 vendored Kronos 失败，多半是隔离依赖未装："
            "pip install -r kronos_vendor/requirements.txt"
        ) from exc
    return Kronos, KronosTokenizer, KronosPredictor


__all__ = ["load_vendor", "KRONOS_VENDOR_ROOT", "PROJECT_ROOT"]
