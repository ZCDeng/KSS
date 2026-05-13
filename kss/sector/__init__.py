"""KSS sector module —— 收盘后板块热度 / 资金轮动复盘.

提供：

- :class:`SectorSnapshot` —— 单日板块数据快照（行业 + 概念 + 北向）
- :func:`load_sector_snapshot` —— 从 Tushare 拉取 + 容错降级
- :class:`KcbOverlay` —— 科创板池子持仓叠加（按行业 / 概念维度）

模块约定：

- 数据层失败一律降级，不抛异常（与 :mod:`kss.data` 契约一致）
- 行业数据源用东财（``moneyflow_ind_dc`` 过滤 ``content_type == '行业'``）
- 概念数据源用同花顺（``moneyflow_cnt_ths``，与 ``stock_names.csv`` 概念字段一致）
"""

from __future__ import annotations

from kss.sector.data_fetcher import SectorSnapshot, load_sector_snapshot

__all__ = ["SectorSnapshot", "load_sector_snapshot"]
