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
MAX_APPEND = 24  # 档 B：用户追加上限（KTD4）
MAX_INDEX_BOARD = 48
DEFAULT_STRIP_METRIC = "limit_max_board"
STRIP_SLOT_IDS = ("strip_0", "strip_1", "strip_2", "strip_3")
# 贴近现网：双 A500ETF + 北向 + 最高连板（保序语义，KTD3）
DEFAULT_STRIP_SLOTS: tuple[str, ...] = (
    "etf_a500_563360",
    "etf_a500_159361",
    "north_money",
    "limit_max_board",
)
# 脚本 INDEX_BOARD 默认 13 码（与 scripts/refresh_market_strip.py 对齐）
DEFAULT_INDEX_BOARD_CODES: tuple[str, ...] = (
    "000001.SH", "399001.SZ", "399006.SZ",
    "000688.SH", "000698.SH", "000680.SH",
    "000300.SH", "000016.SH", "000905.SH",
    "000852.SH", "000510.SH", "932000.CSI",
    "899050.BJ",
)
ALLOWED_KINDS = frozenset({"yfinance", "index_global", "a_share", "hk"})
# 美股 ticker；A 股 600519.SH；港股 00700.HK
CODE_RE = re.compile(r"^[A-Z0-9.^-]{1,16}$")
_TS_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$", re.I)
_HK_CODE_RE = re.compile(r"^\d{1,5}\.HK$", re.I)
ALLOWED_OPS = frozenset({
    "overnight_append",
    "overnight_remove",
    "set_strip_metric",
    "reset_overnight_append",
    "reset_strip_metric",
    "set_strip_slot",
    "reset_strip_slots",
    "index_board_set",
    "index_board_append",
    "index_board_remove",
    "reset_index_board",
})
# 历史别名；四槽模型下 north_money 允许进 strip（KTD2）
NORTH_METRICS = frozenset({"north_money", "north", "hsgt_north"})

def is_valid_overnight_code(code: str, kind: str | None = None) -> bool:
    """分 kind 校验 code 形态。"""
    c = (code or "").strip().upper()
    if not c or not CODE_RE.match(c):
        return False
    k = (kind or "yfinance").strip().lower()
    if k == "a_share":
        return bool(_TS_CODE_RE.match(c))
    if k == "hk":
        return bool(_HK_CODE_RE.match(c))
    return True


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


def default_strip_slots() -> list[dict[str, str]]:
    return [
        {"slot_id": sid, "metric_id": mid}
        for sid, mid in zip(STRIP_SLOT_IDS, DEFAULT_STRIP_SLOTS, strict=True)
    ]


def empty_config() -> dict[str, Any]:
    slots = default_strip_slots()
    return {
        "version": VERSION,
        "updated_at": None,
        "overnight_us": {"append": []},
        "strip_slots": slots,
        # 兼容旧消费者：镜像最后一槽
        "strip_metric": {
            "slot_id": slots[-1]["slot_id"],
            "metric_id": slots[-1]["metric_id"],
        },
        "index_board": None,  # None = 使用默认 13 码
        "degraded": False,
        "error": None,
    }


def _normalize_strip_slots(raw_slots: Any, legacy_metric: str | None) -> list[dict[str, str]]:
    """规范化 4 槽；缺省或旧单 metric 时迁移。catalog 合法性在 apply 时再拦。"""
    defaults = default_strip_slots()
    if isinstance(raw_slots, list) and len(raw_slots) == 4:
        out: list[dict[str, str]] = []
        for i, item in enumerate(raw_slots):
            if not isinstance(item, dict):
                raise ValueError("strip_slots items must be objects")
            sid = str(item.get("slot_id") or STRIP_SLOT_IDS[i]).strip()
            if sid not in STRIP_SLOT_IDS:
                raise ValueError(f"invalid slot_id: {sid}")
            mid = str(item.get("metric_id") or defaults[i]["metric_id"]).strip()
            if not mid:
                mid = defaults[i]["metric_id"]
            out.append({"slot_id": STRIP_SLOT_IDS[i], "metric_id": mid})
        return out

    # 迁移：旧 strip_metric → 默认前 3 + 用户 metric 占 strip_3
    out = default_strip_slots()
    if legacy_metric:
        mid = str(legacy_metric).strip()
        if mid:
            out[-1] = {"slot_id": STRIP_SLOT_IDS[-1], "metric_id": mid}
    return out

def _normalize_index_board(raw: Any) -> dict[str, Any] | None:
    """None = 默认板；否则 {codes: [...]} 全量覆盖，至少 1 项。"""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("index_board must be an object or null")
    codes_raw = raw.get("codes")
    if codes_raw is None:
        return None
    if not isinstance(codes_raw, list):
        raise ValueError("index_board.codes must be a list")
    codes: list[str] = []
    seen: set[str] = set()
    for c in codes_raw:
        code = str(c or "").strip().upper()
        if not code or not CODE_RE.match(code):
            raise ValueError(f"invalid index_board code: {c!r}")
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
    if not codes:
        raise ValueError("index_board.codes must have at least 1 item")
    if len(codes) > MAX_INDEX_BOARD:
        raise ValueError(f"index_board exceeds max {MAX_INDEX_BOARD}")
    return {"codes": codes}


def effective_index_board_codes(cfg: dict[str, Any] | None = None) -> list[str]:
    """用户全量名单或默认 13 码。"""
    body = cfg if cfg is not None else load_config()
    board = body.get("index_board")
    if isinstance(board, dict) and board.get("codes"):
        return [str(c).upper() for c in board["codes"]]
    return list(DEFAULT_INDEX_BOARD_CODES)

def _normalize_code(raw: Any, kind: str | None = None) -> str:
    if not isinstance(raw, str):
        raise ValueError("code must be a string")
    code = raw.strip().upper()
    if not code or not is_valid_overnight_code(code, kind):
        raise ValueError(
            f"invalid code {raw!r} for kind {kind!r}"
        )
    return code


def _normalize_append_item(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("append item must be an object")
    kind = str(item.get("kind") or "yfinance").strip().lower()
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"invalid kind {kind!r}; allowed={sorted(ALLOWED_KINDS)}")
    code = _normalize_code(item.get("code"), kind)
    name = item.get("name")
    if name is not None and not isinstance(name, str):
        raise ValueError("name must be a string or null")
    kind_source = item.get("kind_source") or "candidate_table"
    if kind_source not in ("candidate_table", "ai_inferred", "catalog", "nl"):
        # 兼容扩展来源；未知时降为 candidate_table
        kind_source = "candidate_table"
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
    legacy_mid = str(strip.get("metric_id") or "").strip() or None
    slots = _normalize_strip_slots(raw.get("strip_slots"), legacy_mid)
    last = slots[-1]
    index_board = _normalize_index_board(raw.get("index_board"))

    return {
        "version": int(raw.get("version") or VERSION),
        "updated_at": raw.get("updated_at"),
        "overnight_us": {"append": append},
        "strip_slots": slots,
        "strip_metric": {
            "slot_id": last["slot_id"],
            "metric_id": last["metric_id"],
            "label_override": strip.get("label_override"),
        },
        "index_board": index_board,
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
        "strip_slots": cleaned["strip_slots"],
        "strip_metric": cleaned["strip_metric"],
        "index_board": cleaned.get("index_board"),
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
    slots: list[dict[str, str]] = list(
        working.get("strip_slots") or default_strip_slots()
    )
    if len(slots) != 4:
        slots = default_strip_slots()
    index_board = working.get("index_board")
    defaults = default_codes()
    from kss.ui_surface.resolve import METRIC_CATALOG

    def _set_slot(slot_id: str, mid: str) -> None:
        nonlocal slots
        if slot_id not in STRIP_SLOT_IDS:
            raise ValueError(f"invalid slot_id: {slot_id}")
        if mid not in METRIC_CATALOG:
            raise ValueError(f"unknown metric_id: {mid}")
        idx = STRIP_SLOT_IDS.index(slot_id)
        slots = list(slots)
        slots[idx] = {"slot_id": slot_id, "metric_id": mid}

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
                # 兼容：写最后一槽
                mid = str(op.get("metric_id") or "").strip()
                if not mid:
                    raise ValueError("metric_id required")
                _set_slot(STRIP_SLOT_IDS[-1], mid)

            elif name == "set_strip_slot":
                sid = str(op.get("slot_id") or "").strip()
                mid = str(op.get("metric_id") or "").strip()
                if not sid:
                    raise ValueError("slot_id required")
                if not mid:
                    raise ValueError("metric_id required")
                _set_slot(sid, mid)

            elif name == "reset_overnight_append":
                append = []

            elif name == "reset_strip_metric":
                _set_slot(STRIP_SLOT_IDS[-1], DEFAULT_STRIP_METRIC)

            elif name == "reset_strip_slots":
                slots = default_strip_slots()

            elif name == "index_board_set":
                codes = op.get("codes")
                index_board = _normalize_index_board({"codes": codes})

            elif name == "index_board_append":
                code = str(op.get("code") or "").strip().upper()
                if not code or not CODE_RE.match(code):
                    raise ValueError(f"invalid code: {op.get('code')!r}")
                cur = list(
                    (index_board or {}).get("codes")
                    or DEFAULT_INDEX_BOARD_CODES
                )
                cur = [str(c).upper() for c in cur]
                if code in cur:
                    continue  # 幂等
                if len(cur) >= MAX_INDEX_BOARD:
                    raise ValueError(f"index_board exceeds max {MAX_INDEX_BOARD}")
                cur.append(code)
                index_board = {"codes": cur}

            elif name == "index_board_remove":
                code = str(op.get("code") or "").strip().upper()
                if not code:
                    raise ValueError("code required")
                cur = list(
                    (index_board or {}).get("codes")
                    or DEFAULT_INDEX_BOARD_CODES
                )
                cur = [str(c).upper() for c in cur if str(c).upper() != code]
                if not cur:
                    raise ValueError("index_board.codes must have at least 1 item")
                index_board = {"codes": cur}

            elif name == "reset_index_board":
                index_board = None

        body = {
            "version": VERSION,
            "overnight_us": {"append": append},
            "strip_slots": slots,
            "strip_metric": {
                "slot_id": slots[-1]["slot_id"],
                "metric_id": slots[-1]["metric_id"],
            },
            "index_board": index_board,
        }
        saved = save_config(body)
        return {"ok": True, "config": saved}
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "config": load_config()}
