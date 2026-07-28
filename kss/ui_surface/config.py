"""盯盘 surface L3 配置：schema + 原子写（仿 track_keywords 单文件形状）.

路径：``$KSS_STATE_ROOT/storage/ui_surface/dashboard_v1.json``
"""

from __future__ import annotations

import json
import logging
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VERSION = 1
MAX_APPEND = 8
DEFAULT_STRIP_METRIC = "limit_max_board"
ALLOWED_KINDS = frozenset({"yfinance", "index_global"})
CODE_RE = re.compile(r"^[A-Z0-9.^-]{1,12}$")
ALLOWED_OPS = frozenset({
    "overnight_append",
    "overnight_remove",
    "set_strip_metric",
    "reset_overnight_append",
    "reset_strip_metric",
})
# 北向已在第一行固定展示；不得作小卡 metric
NORTH_METRICS = frozenset({"north_money", "north", "hsgt_north"})


def _state_root() -> Path:
    env = os.environ.get("KSS_STATE_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def _config_path() -> Path:
    return _state_root() / "storage" / "ui_surface" / "dashboard_v1.json"


def default_codes() -> frozenset[str]:
    """系统默认隔夜 code（大写）。"""
    from scripts.overnight_us_universe import OVERNIGHT_US_UNIVERSE

    return frozenset(str(r["code"]).upper() for r in OVERNIGHT_US_UNIVERSE)


def empty_config() -> dict[str, Any]:
    return {
        "version": VERSION,
        "updated_at": None,
        "overnight_us": {"append": []},
        "strip_metric": {"slot_id": "strip_extra_1", "metric_id": DEFAULT_STRIP_METRIC},
        "degraded": False,
        "error": None,
    }


def _normalize_code(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ValueError("code must be a string")
    code = raw.strip().upper()
    if not code or not CODE_RE.match(code):
        raise ValueError(
            f"invalid code {raw!r}: must match ^[A-Z0-9.^-]{{1,12}}$"
        )
    return code


def _normalize_append_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("append item must be an object")
    code = _normalize_code(item.get("code"))
    kind = str(item.get("kind") or "yfinance").strip().lower()
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"invalid kind {kind!r}; allowed={sorted(ALLOWED_KINDS)}")
    name = item.get("name")
    if name is not None and not isinstance(name, str):
        raise ValueError("name must be a string or null")
    kind_source = item.get("kind_source") or "candidate_table"
    if kind_source not in ("candidate_table", "ai_inferred"):
        raise ValueError("kind_source must be candidate_table or ai_inferred")
    out: dict[str, Any] = {
        "id": str(item.get("id") or f"usr-{code.lower()}"),
        "code": code,
        "name": (name or code).strip() or code,
        "kind": kind,
        "kind_source": kind_source,
        "added_via": str(item.get("added_via") or "plus"),
    }
    if item.get("resolved_at") is not None:
        out["resolved_at"] = str(item["resolved_at"])
    if item.get("probe_close") is not None:
        try:
            out["probe_close"] = float(item["probe_close"])
        except (TypeError, ValueError) as exc:
            raise ValueError("probe_close must be a number") from exc
    return out


def validate_config_body(raw: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化配置 body；非法字段 raise ValueError。"""
    if not isinstance(raw, dict):
        raise ValueError("config must be an object")
    overnight = raw.get("overnight_us") or {}
    if not isinstance(overnight, dict):
        raise ValueError("overnight_us must be an object")
    append_raw = overnight.get("append") or []
    if not isinstance(append_raw, list):
        raise ValueError("overnight_us.append must be a list")
    defaults = default_codes()
    append: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in append_raw:
        norm = _normalize_append_item(item)
        code = norm["code"]
        if code in defaults:
            raise ValueError(f"cannot append system default code: {code}")
        if code in seen:
            raise ValueError(f"duplicate append code: {code}")
        seen.add(code)
        append.append(norm)
    if len(append) > MAX_APPEND:
        raise ValueError(f"append exceeds max {MAX_APPEND}")

    strip = raw.get("strip_metric") or {}
    if not isinstance(strip, dict):
        raise ValueError("strip_metric must be an object")
    metric_id = str(strip.get("metric_id") or DEFAULT_STRIP_METRIC).strip()
    if not metric_id:
        metric_id = DEFAULT_STRIP_METRIC
    if metric_id in NORTH_METRICS:
        raise ValueError("第一行已固定展示北向资金")

    return {
        "version": int(raw.get("version") or VERSION),
        "updated_at": raw.get("updated_at"),
        "overnight_us": {"append": append},
        "strip_metric": {
            "slot_id": str(strip.get("slot_id") or "strip_extra_1"),
            "metric_id": metric_id,
            "label_override": strip.get("label_override"),
        },
        "degraded": False,
        "error": None,
    }


def load_config() -> dict[str, Any]:
    """加载配置；缺文件→默认；坏 JSON→降级默认 + degraded。"""
    path = _config_path()
    if not path.is_file():
        return empty_config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("ui_surface config corrupt %s: %s", path, exc)
        cfg = empty_config()
        cfg["degraded"] = True
        cfg["error"] = f"corrupt_json: {exc}"
        return cfg
    try:
        return validate_config_body(raw if isinstance(raw, dict) else {})
    except ValueError as exc:
        logger.warning("ui_surface config invalid %s: %s", path, exc)
        cfg = empty_config()
        cfg["degraded"] = True
        cfg["error"] = f"invalid_config: {exc}"
        return cfg


def save_config(body: dict[str, Any]) -> dict[str, Any]:
    """校验后原子写；返回规范化配置。"""
    cleaned = validate_config_body(body)
    cleaned["updated_at"] = datetime.now(timezone.utc).isoformat()
    cleaned["degraded"] = False
    cleaned["error"] = None
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": cleaned["version"],
        "updated_at": cleaned["updated_at"],
        "overnight_us": cleaned["overnight_us"],
        "strip_metric": cleaned["strip_metric"],
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return cleaned


def apply_patch(ops: list[dict[str, Any]] | None) -> dict[str, Any]:
    """对当前配置应用闭集 ops；返回 {ok, config, error?}。

    幂等：重复 overnight_append 同一 code 视为成功（不 error）。
    """
    if ops is None:
        ops = []
    if not isinstance(ops, list):
        return {"ok": False, "error": "ops must be a list", "config": load_config()}

    cfg = load_config()
    if cfg.get("degraded"):
        # 从损坏文件恢复：以空配置为底，仍允许用户写入
        working = empty_config()
    else:
        working = deepcopy(cfg)

    append: list[dict[str, Any]] = list(working["overnight_us"]["append"])
    metric_id = working["strip_metric"]["metric_id"]
    defaults = default_codes()

    try:
        for op in ops:
            if not isinstance(op, dict):
                raise ValueError("each op must be an object")
            name = str(op.get("op") or "").strip()
            if name not in ALLOWED_OPS:
                raise ValueError(f"unknown op: {name!r}")

            if name == "overnight_append":
                item = {
                    "code": op.get("code"),
                    "name": op.get("name"),
                    "kind": op.get("kind") or "yfinance",
                    "kind_source": op.get("kind_source") or "candidate_table",
                    "added_via": op.get("added_via") or "plus",
                    "resolved_at": op.get("resolved_at"),
                    "probe_close": op.get("probe_close"),
                    "id": op.get("id"),
                }
                norm = _normalize_append_item(item)
                code = norm["code"]
                if code in defaults:
                    raise ValueError(f"cannot append system default code: {code}")
                existing_codes = {a["code"] for a in append}
                if code in existing_codes:
                    continue  # 幂等成功
                if len(append) >= MAX_APPEND:
                    raise ValueError(f"append exceeds max {MAX_APPEND}")
                append.append(norm)

            elif name == "overnight_remove":
                code = _normalize_code(op.get("code"))
                if code in defaults:
                    raise ValueError(f"cannot remove system default code: {code}")
                append = [a for a in append if a["code"] != code]

            elif name == "set_strip_metric":
                mid = str(op.get("metric_id") or "").strip()
                if not mid:
                    raise ValueError("metric_id required")
                if mid in NORTH_METRICS:
                    raise ValueError("第一行已固定展示北向资金")
                # catalog 校验在 apply 调用方或 resolve 层；此处拦 north
                from kss.ui_surface.resolve import METRIC_CATALOG

                if mid not in METRIC_CATALOG:
                    raise ValueError(f"unknown metric_id: {mid}")
                metric_id = mid

            elif name == "reset_overnight_append":
                append = []

            elif name == "reset_strip_metric":
                metric_id = DEFAULT_STRIP_METRIC

        body = {
            "version": VERSION,
            "overnight_us": {"append": append},
            "strip_metric": {
                "slot_id": "strip_extra_1",
                "metric_id": metric_id,
            },
        }
        saved = save_config(body)
        return {"ok": True, "config": saved}
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "config": load_config()}
