"""紫苏叶产业链卡位评分模块.

方法论来源: Serenity「紫苏叶理论」— 沿产业链下钻找不可替代的卡脖子供应商.
设计文档: docs/plans/2026-06-02-001-feat-perilla-leaf-supply-chain-scoring-plan.md
"""

from __future__ import annotations

from kss.supply_chain.registry import ChainRegistry, StockChainInfo
from kss.supply_chain.scoring import compute_perilla_score

__all__ = ["ChainRegistry", "StockChainInfo", "compute_perilla_score"]
