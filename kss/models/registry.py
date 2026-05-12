"""模型注册中心 —— 版本化管理与持久化."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kss.models.base import ModelBase


class ModelRegistry:
    """模型版本注册中心.

    负责模型的命名保存、按时间戳版本化、列表查询与自动清理。
    目录结构::

        storage_dir/
        ├── model_a/
        │   ├── 20250510_143022.joblib
        │   ├── 20250511_091530.joblib
        │   └── metadata.json
        └── model_b/
            └── ...
    """

    def __init__(self, storage_dir: str = "./kss/storage/models") -> None:
        """初始化注册中心.

        Args:
            storage_dir: 模型存储根目录.
        """
        self.storage_dir = Path(storage_dir).resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #

    def _model_dir(self, name: str) -> Path:
        """获取某模型的专属目录."""
        d = self.storage_dir / name
        d.mkdir(exist_ok=True)
        return d

    @staticmethod
    def _timestamp() -> str:
        """生成 UTC 时间戳字符串."""
        return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def _metadata_path(model_dir: Path) -> Path:
        """元数据文件路径."""
        return model_dir / "metadata.json"

    def _read_metadata(self, model_dir: Path) -> list[dict[str, Any]]:
        """读取某模型的元数据列表."""
        meta_path = self._metadata_path(model_dir)
        if meta_path.exists():
            with meta_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def _write_metadata(self, model_dir: Path, records: list[dict[str, Any]]) -> None:
        """写入某模型的元数据列表."""
        meta_path = self._metadata_path(model_dir)
        with meta_path.open("w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------ #
    # 公共接口
    # ------------------------------------------------------------------ #

    def save(
        self,
        model: ModelBase,
        name: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """保存模型并生成版本.

        Args:
            model: 待保存的模型实例.
            name: 模型名称（如 ``lgb_kcb50_10d``）.
            metadata: 附加元数据字典（如训练日期、AUC 等）.

        Returns:
            生成的 model_id（格式 ``{name}_{timestamp}``）.
        """
        model_dir = self._model_dir(name)
        ts = self._timestamp()
        model_id = f"{name}_{ts}"
        file_path = model_dir / f"{ts}.joblib"

        model.save(str(file_path))

        records = self._read_metadata(model_dir)
        records.append({
            "model_id": model_id,
            "name": name,
            "timestamp": ts,
            "file": str(file_path.name),
            "metadata": metadata or {},
        })
        self._write_metadata(model_dir, records)
        return model_id

    def load(self, name_or_id: str) -> ModelBase:
        """加载模型.

        若传入 ``name``（不含下划线时间戳），加载该名称下最新版本；
        若传入完整 ``model_id``（如 ``lgb_kcb50_10d_20250510_143022``），加载指定版本。

        Args:
            name_or_id: 模型名称或完整 model_id.

        Returns:
            已加载的模型实例（当前返回 ``LightGBMModel``，调用方需预先知道类型）。

        Raises:
            FileNotFoundError: 找不到对应模型文件或元数据。
            ValueError: 无法解析 model_id。
        """
        # 延迟导入，避免循环依赖
        from kss.models.lightgbm_model import LightGBMModel

        parts = name_or_id.rsplit("_", 2)
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            # 完整 model_id
            name, ts = parts[0], f"{parts[1]}_{parts[2]}"
            model_dir = self._model_dir(name)
            file_path = model_dir / f"{ts}.joblib"
            if not file_path.exists():
                raise FileNotFoundError(f"找不到模型文件: {file_path}")
            model = LightGBMModel()
            model.load(str(file_path))
            return model

        # 仅传入 name，加载最新版本
        name = name_or_id
        model_dir = self._model_dir(name)
        latest_path = self.get_latest(name)
        model = LightGBMModel()
        model.load(str(latest_path))
        return model

    def list(self, name: str | None = None) -> list[dict[str, Any]]:
        """列出已保存的模型.

        Args:
            name: 模型名称过滤；若为 ``None`` 则返回所有模型.

        Returns:
            模型元数据记录列表，按时间戳降序排列。
        """
        if name is not None:
            records = self._read_metadata(self._model_dir(name))
            return sorted(records, key=lambda r: r["timestamp"], reverse=True)

        all_records: list[dict[str, Any]] = []
        for subdir in self.storage_dir.iterdir():
            if subdir.is_dir():
                all_records.extend(self._read_metadata(subdir))
        return sorted(all_records, key=lambda r: r["timestamp"], reverse=True)

    def get_latest(self, name: str) -> Path:
        """返回最新模型文件路径.

        Args:
            name: 模型名称.

        Returns:
            最新模型文件 ``Path``。

        Raises:
            FileNotFoundError: 该模型无任何保存记录。
        """
        model_dir = self._model_dir(name)
        records = self._read_metadata(model_dir)
        if not records:
            raise FileNotFoundError(f"模型 '{name}' 尚未保存任何版本.")

        latest = max(records, key=lambda r: r["timestamp"])
        return model_dir / latest["file"]

    def cleanup(self, name: str, keep: int = 5) -> list[str]:
        """清理旧版本，仅保留最近 N 个.

        Args:
            name: 模型名称.
            keep: 保留的最新版本数量，默认 5.

        Returns:
            被删除的 model_id 列表.
        """
        model_dir = self._model_dir(name)
        records = self._read_metadata(model_dir)
        if len(records) <= keep:
            return []

        # 按时间戳升序，保留后 keep 个（最新的）
        sorted_records = sorted(records, key=lambda r: r["timestamp"])
        to_remove = sorted_records[:-keep]
        kept = sorted_records[-keep:]

        removed_ids: list[str] = []
        for rec in to_remove:
            file_path = model_dir / rec["file"]
            try:
                file_path.unlink(missing_ok=True)
                removed_ids.append(rec["model_id"])
            except OSError:
                pass

        self._write_metadata(model_dir, kept)
        return removed_ids
