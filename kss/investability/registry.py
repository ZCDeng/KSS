"""可投资地图节点树的加载与查询.

从 ``kss/config/investability_map.yaml`` 读 103 个子行业节点, 每个节点带一个
主色(五色之一或 ``pending``)、可选次色、所属主轴与组、读法、限定说明、源文依据
与最近复核日期.

降级纪律(plan KTD7): 配置文件缺失返回空注册表 + warning, 不抛异常 ——
打包版部分安装时抛异常会让整个应用起不来. YAML 语法错误与顶层结构错误抛
:class:`InvestabilityMapError`, 因为那是人工手改配置后必须被看见的失败.
单个节点字段不合法只告警跳过该节点, 其余节点仍可用.

红不是行业色(plan R2). 主色与次色都只能取 ``palette`` 里声明的五个键;
次色取红会因为红不在 palette 里而被拒, 该节点跳过.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "investability_map.yaml"

#: 未定色标记. 源文未给到节点级色标, 或给出互相冲突的两处标注(plan R4).
PENDING = "pending"

#: 节点复核日期距今超过这个天数即视为陈旧(plan R23).
STALE_AFTER_DAYS = 120


class InvestabilityMapError(ValueError):
    """配置文件存在但格式不合法(不是文件缺失)."""


@dataclass(frozen=True)
class PaletteColor:
    """一个行业色.

    Attributes:
        key: 稳定机器键, 如 ``deep_green``. 桌面端与 agent 都按它取色与文案.
        label: 中文短标签, 如「深绿」. 色在任何落点都须与它成对出现(plan R2).
        meaning: 该色代表的暴露语义, 如「底座 / 低暴露」.
    """

    key: str
    label: str
    meaning: str

    def as_dict(self) -> dict[str, str]:
        """转字典(桥接返回值用)."""
        return {"key": self.key, "label": self.label, "meaning": self.meaning}


@dataclass(frozen=True)
class MapNode:
    """一个子行业节点.

    Attributes:
        node_id: 稳定标识, 形如 ``compute.04``. 个股标注按它挂载.
        name: 中文节点名, 照录源文.
        axis: 所属主轴键, 三取一.
        group: 所属组键, 决定泳道归属(plan R16).
        primary_color: 主色键, 五色之一或 ``pending``. 决定该节点及挂在其下
            个股的显示色(plan R3).
        secondary_color: 次色键, 五色之一或空. 只在节点详情里作限定说明用,
            不参与渲染. 红不得作次色(plan R3).
        tier: ``first`` / ``second`` / 空. 仅新一代信息技术在源表中给出,
            缺失时不参与排布(plan R5).
        reading: 源文给出的读法一句, 可空.
        qualifier: 限定说明. 源文的红色限定改写进这里(plan R3).
        source_ref: 定色所依据的源文章节与片段.
        last_reviewed: 最近复核日期(ISO 8601).
    """

    node_id: str
    name: str
    axis: str
    group: str
    primary_color: str
    secondary_color: str = ""
    tier: str = ""
    reading: str = ""
    qualifier: str = ""
    source_ref: str = ""
    last_reviewed: str = ""

    @property
    def is_pending(self) -> bool:
        """主色是否未定."""
        return self.primary_color == PENDING

    def as_dict(self) -> dict[str, Any]:
        """转字典(桥接返回值用)."""
        return {
            "nodeId": self.node_id,
            "name": self.name,
            "axis": self.axis,
            "group": self.group,
            "primaryColor": self.primary_color,
            "secondaryColor": self.secondary_color,
            "tier": self.tier,
            "reading": self.reading,
            "qualifier": self.qualifier,
            "sourceRef": self.source_ref,
            "lastReviewed": self.last_reviewed,
            "isPending": self.is_pending,
        }


@dataclass(frozen=True)
class Axis:
    """一条主轴及其所属组."""

    key: str
    label: str
    source_ref: str
    groups: dict[str, str] = field(default_factory=dict)


def _empty_map() -> InvestabilityMap:
    """构造一个空但合法的注册表, 供文件缺失时降级返回."""
    return InvestabilityMap(nodes={}, palette={}, axes={}, source_version="")


class InvestabilityMap:
    """节点树注册表 —— 加载 YAML、按 id 或组查询、算陈旧度."""

    def __init__(
        self,
        nodes: dict[str, MapNode],
        palette: dict[str, PaletteColor],
        axes: dict[str, Axis],
        source_version: str,
    ) -> None:
        self._nodes = nodes
        self._palette = palette
        self._axes = axes
        self._source_version = source_version

    # ------------------------------------------------------------------ #
    # 构造
    # ------------------------------------------------------------------ #

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> InvestabilityMap:
        """从 YAML 加载.

        Args:
            path: 配置路径; ``None`` 走 ``kss/config/investability_map.yaml``.

        Returns:
            注册表. 文件缺失返回空注册表并告警, 不抛异常.

        Raises:
            InvestabilityMapError: YAML 语法错误, 或顶层缺 ``nodes`` / ``palette``.
        """
        p = Path(path) if path is not None else _CONFIG_PATH
        if not p.exists():
            logger.warning("investability_map.yaml 不存在 (%s), 地图将为空", p)
            return _empty_map()

        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise InvestabilityMapError(f"investability_map.yaml 解析失败: {exc}") from exc

        if not isinstance(raw, dict):
            raise InvestabilityMapError(
                f"investability_map.yaml 顶层必须是 dict, 实际: {type(raw).__name__}"
            )
        for required in ("palette", "nodes"):
            if required not in raw:
                raise InvestabilityMapError(
                    f"investability_map.yaml 顶层缺 '{required}' 键"
                )

        palette = cls._parse_palette(raw["palette"])
        axes = cls._parse_axes(raw.get("axes") or {})
        nodes = cls._parse_nodes(raw["nodes"], palette=palette, axes=axes)

        logger.info(
            "加载可投资地图 %d 个节点, 其中未定色 %d 个",
            len(nodes),
            sum(1 for n in nodes.values() if n.is_pending),
        )
        return cls(
            nodes=nodes,
            palette=palette,
            axes=axes,
            source_version=str(raw.get("source_version", "")),
        )

    @staticmethod
    def _parse_palette(raw: Any) -> dict[str, PaletteColor]:
        """解析色板. 结构不合法直接抛错, 色板是整棵树的取值域."""
        if not isinstance(raw, dict) or not raw:
            raise InvestabilityMapError("palette 必须是非空 dict")
        out: dict[str, PaletteColor] = {}
        for key, body in raw.items():
            body = body if isinstance(body, dict) else {}
            out[str(key)] = PaletteColor(
                key=str(key),
                label=str(body.get("label", "")),
                meaning=str(body.get("meaning", "")),
            )
        return out

    @staticmethod
    def _parse_axes(raw: Any) -> dict[str, Axis]:
        """解析主轴与组. 缺失时返回空 dict, 节点侧的组校验随之放宽."""
        if not isinstance(raw, dict):
            logger.warning("axes 字段不是 dict, 忽略")
            return {}
        out: dict[str, Axis] = {}
        for key, body in raw.items():
            body = body if isinstance(body, dict) else {}
            groups = body.get("groups")
            out[str(key)] = Axis(
                key=str(key),
                label=str(body.get("label", "")),
                source_ref=str(body.get("source_ref", "")),
                groups={str(g): str(n) for g, n in (groups or {}).items()},
            )
        return out

    @staticmethod
    def _parse_nodes(
        raw: Any,
        palette: dict[str, PaletteColor],
        axes: dict[str, Axis],
    ) -> dict[str, MapNode]:
        """逐条解析节点; 单条不合法告警跳过, 不影响其余节点."""
        if not isinstance(raw, list):
            raise InvestabilityMapError(
                f"nodes 必须是 list, 实际: {type(raw).__name__}"
            )

        valid_primary = set(palette) | {PENDING}
        out: dict[str, MapNode] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                logger.warning("节点条目不是 dict, 跳过: %r", entry)
                continue
            node_id = str(entry.get("node_id", "")).strip()
            if not node_id:
                logger.warning("节点缺 node_id, 跳过: %r", entry)
                continue
            if node_id in out:
                logger.warning("node_id 重复, 跳过后一条: %s", node_id)
                continue

            primary = str(entry.get("primary_color", "")).strip()
            if primary not in valid_primary:
                logger.warning(
                    "节点 %s 主色 %r 不在色板内, 跳过该节点", node_id, primary
                )
                continue

            secondary = str(entry.get("secondary_color", "") or "").strip()
            if secondary and secondary not in palette:
                logger.warning(
                    "节点 %s 次色 %r 不在色板内(红不得作次色), 跳过该节点",
                    node_id,
                    secondary,
                )
                continue

            axis = str(entry.get("axis", "")).strip()
            group = str(entry.get("group", "")).strip()
            if axes:
                if axis not in axes:
                    logger.warning("节点 %s 主轴 %r 未声明, 跳过", node_id, axis)
                    continue
                if group not in axes[axis].groups:
                    logger.warning(
                        "节点 %s 的组 %r 不属于主轴 %s, 跳过", node_id, group, axis
                    )
                    continue

            out[node_id] = MapNode(
                node_id=node_id,
                name=str(entry.get("name", "")),
                axis=axis,
                group=group,
                primary_color=primary,
                secondary_color=secondary,
                tier=str(entry.get("tier", "") or ""),
                reading=str(entry.get("reading", "") or ""),
                qualifier=str(entry.get("qualifier", "") or ""),
                source_ref=str(entry.get("source_ref", "") or ""),
                last_reviewed=str(entry.get("last_reviewed", "") or ""),
            )
        return out

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #

    @property
    def source_version(self) -> str:
        """当前生效的源文版本号(plan R23 页头显示用)."""
        return self._source_version

    @property
    def palette(self) -> dict[str, PaletteColor]:
        """五色色板."""
        return dict(self._palette)

    @property
    def axes(self) -> dict[str, Axis]:
        """主轴与组."""
        return dict(self._axes)

    def __len__(self) -> int:
        """节点总数."""
        return len(self._nodes)

    def get(self, node_id: str) -> MapNode | None:
        """按 id 查节点; 不存在返回 ``None``."""
        return self._nodes.get(str(node_id).strip())

    def all_nodes(self) -> list[MapNode]:
        """按 YAML 原序返回全部节点."""
        return list(self._nodes.values())

    def by_group(self, group: str) -> list[MapNode]:
        """返回某组下的全部节点, 保持 YAML 原序."""
        return [n for n in self._nodes.values() if n.group == group]

    def by_axis(self, axis: str) -> list[MapNode]:
        """返回某主轴下的全部节点, 保持 YAML 原序."""
        return [n for n in self._nodes.values() if n.axis == axis]

    def by_color(self, color_key: str) -> list[MapNode]:
        """返回主色为某键的全部节点; 传 ``pending`` 取未定色节点."""
        return [n for n in self._nodes.values() if n.primary_color == color_key]

    def pending_nodes(self) -> list[MapNode]:
        """返回全部未定色节点(plan R4)."""
        return self.by_color(PENDING)

    def stale_node_ids(
        self,
        as_of: date,
        max_age_days: int = STALE_AFTER_DAYS,
    ) -> set[str]:
        """返回复核日期距 ``as_of`` 超过 ``max_age_days`` 的节点 id 集合.

        判定放在 Python 侧, 桌面端只渲染结果(plan KTD2). 复核日期缺失或不可解析
        的节点一律视为陈旧 —— 无法证明它新, 就不该显示成新的.

        Args:
            as_of: 判定基准日.
            max_age_days: 超过多少天算陈旧, 默认 120(plan R23).

        Returns:
            陈旧节点的 id 集合.
        """
        stale: set[str] = set()
        for node in self._nodes.values():
            reviewed = _parse_date(node.last_reviewed)
            if reviewed is None or (as_of - reviewed).days > max_age_days:
                stale.add(node.node_id)
        return stale

    def oldest_reviewed(self) -> str:
        """返回全表最旧的复核日期(plan R23 页头显示用); 无可解析日期返回空串."""
        dates = [d for d in (_parse_date(n.last_reviewed) for n in self._nodes.values()) if d]
        return min(dates).isoformat() if dates else ""


def _parse_date(value: str) -> date | None:
    """把 ISO 8601 日期串解析成 ``date``; 不可解析返回 ``None``."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
