"""盯盘页可配置 surface（L3 实例配置 + resolve）。"""

from __future__ import annotations

from kss.ui_surface.config import (
    DEFAULT_STRIP_METRIC,
    MAX_APPEND,
    apply_patch,
    default_codes,
    load_config,
    save_config,
)
from kss.ui_surface.resolve import (
    CANDIDATE_OVERNIGHT,
    METRIC_CATALOG,
    effective_overnight_universe,
    probe_overnight_code,
    resolve_metric_props,
    resolve_overnight_preview,
)

__all__ = [
    "CANDIDATE_OVERNIGHT",
    "DEFAULT_STRIP_METRIC",
    "MAX_APPEND",
    "METRIC_CATALOG",
    "apply_patch",
    "default_codes",
    "effective_overnight_universe",
    "load_config",
    "probe_overnight_code",
    "resolve_metric_props",
    "resolve_overnight_preview",
    "save_config",
]
