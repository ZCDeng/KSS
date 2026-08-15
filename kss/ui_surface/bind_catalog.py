"""Bind Catalog：盯盘可绑目录（唯一可绑真源）。

物化：``$KSS_STATE_ROOT/storage/ui_surface/bind_catalog_v{CATALOG_VERSION}.json``
缺文件时 ``build_catalog()`` 内存生成。

改了目录内容（新增槽位/新增种子）就 **bump CATALOG_VERSION**：文件名带版本号，
旧物化文件不会被读到，装了新版的机器自动重建。否则代码改了、线上仍读老 JSON，
改动等于没上线。
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kss.ui_surface.config import DEFAULT_INDEX_BOARD_CODES

logger = logging.getLogger(__name__)

CATALOG_VERSION = 2  # v2：补齐 index_board 槽位目录
SLOT_OVERNIGHT = "overnight_marquee"
SLOT_STRIP = "strip_metric"
SLOT_INDEX_BOARD = "index_board"
DEFAULT_SEARCH_LIMIT = 50

# A/H 代码常见形态
_TS_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$", re.I)
_HK_CODE_RE = re.compile(r"^\d{1,5}\.HK$", re.I)
_US_CODE_RE = re.compile(r"^[A-Z][A-Z0-9.^-]{0,11}$")

# 热指标：metric_id → meta（含 index_code 或 limit 字段）
HOT_METRICS: dict[str, dict[str, Any]] = {
    "limit_max_board": {
        "title": "最高连板",
        "description": "涨停板最高连板高度",
        "aliases": ["最高连板", "连板高度", "连板", "maxboard", "limit_max_board"],
        "kind": "breadth_metric",
        "market": "CN",
        "domain": "metric_hot",
    },
    "limit_seal_rate": {
        "title": "封板率",
        "description": "涨停封板率",
        "aliases": ["封板率", "封板", "sealrate", "limit_seal_rate"],
        "kind": "breadth_metric",
        "market": "CN",
        "domain": "metric_hot",
    },
    "limit_up_count": {
        "title": "涨停家数",
        "description": "涨停家数（limitBoard.total）",
        "aliases": ["涨停家数", "涨停数", "limit_up_count"],
        "kind": "breadth_metric",
        "market": "CN",
        "domain": "metric_hot",
    },
    "limit_break_rate": {
        "title": "破板率",
        "description": "涨停破板率（limitBoard.breakRate）",
        "aliases": ["破板率", "破板", "limit_break_rate"],
        "kind": "breadth_metric",
        "market": "CN",
        "domain": "metric_hot",
    },
    "index_kcb50": {
        "title": "科创50",
        "description": "科创50 收盘与涨跌",
        "aliases": ["科创50", "科创", "kcb50", "index_kcb50"],
        "kind": "index",
        "market": "CN",
        "domain": "metric_hot",
        "index_code": "000688.SH",
    },
    "index_cyb": {
        "title": "创业板指",
        "description": "创业板指 收盘与涨跌",
        "aliases": ["创业板指", "创业板", "cyb", "index_cyb"],
        "kind": "index",
        "market": "CN",
        "domain": "metric_hot",
        "index_code": "399006.SZ",
    },
    "index_sse": {
        "title": "上证指数",
        "description": "上证指数 收盘与涨跌",
        "aliases": ["上证指数", "上证", "沪指", "index_sse", "000001.SH"],
        "kind": "index",
        "market": "CN",
        "domain": "metric_hot",
        "index_code": "000001.SH",
    },
    "index_szse": {
        "title": "深证成指",
        "description": "深证成指 收盘与涨跌",
        "aliases": ["深证成指", "深成指", "深证", "index_szse", "399001.SZ"],
        "kind": "index",
        "market": "CN",
        "domain": "metric_hot",
        "index_code": "399001.SZ",
    },
    "index_a50": {
        "title": "富时中国A50",
        "description": "富时中国A50（XIN9 / strip 或 index_global）",
        "aliases": [
            "富时中国A50", "富时A50", "A50", "a50", "富时中国A50指数",
            "index_a50", "XIN9", "xin9",
        ],
        "kind": "index",
        "market": "GLOBAL",
        "domain": "metric_hot",
        "index_code": "XIN9",
    },
    "north_money": {
        "title": "北向资金",
        "description": "沪深港通北向净流入",
        "aliases": ["北向资金", "北向", "north", "north_money", "hsgt"],
        "kind": "breadth_metric",
        "market": "CN",
        "domain": "metric_hot",
    },
    "etf_a500_563360": {
        "title": "A500ETF",
        "description": "A500ETF 563360.SH",
        "aliases": ["A500ETF", "A500", "563360", "etf_a500_563360"],
        "kind": "etf",
        "market": "CN",
        "domain": "metric_hot",
        "etf_code": "563360.SH",
    },
    "etf_a500_159361": {
        "title": "A500ETF",
        "description": "A500ETF 159361.SZ",
        "aliases": ["159361", "etf_a500_159361"],
        "kind": "etf",
        "market": "CN",
        "domain": "metric_hot",
        "etf_code": "159361.SZ",
    },
}

# 指数一览（index_board）展示名 + 额外别名：code → (展示名, 别名)
#
# 码集真源是 ``config.DEFAULT_INDEX_BOARD_CODES``，本表只补名字，不自带码集
# ——两处对不上由 test_bind_catalog 拦截。这些码同时是
# ``scripts/refresh_market_strip.py`` 的 INDEX_BOARD（唯一给 indexBoard 供价的
# 抓取表）：不在那张表里的码即使绑上，effective_index_board_quotes 也只能给出
# close=None 的骨架行，所以 picker 不提供。用户仍可用代码 ad-hoc 追加。
INDEX_BOARD_NAMES: dict[str, tuple[str, tuple[str, ...]]] = {
    "000001.SH": ("上证指数", ("上证", "沪指")),
    "399001.SZ": ("深证成指", ("深成指", "深证")),
    "399006.SZ": ("创业板指", ("创业板",)),
    "000688.SH": ("科创50", ("科创",)),
    "000698.SH": ("科创100", ()),
    "000680.SH": ("科创综指", ("科创综",)),
    "000300.SH": ("沪深300", ("沪深",)),
    "000016.SH": ("上证50", ()),
    "000905.SH": ("中证500", ()),
    "000852.SH": ("中证1000", ()),
    "000510.SH": ("中证A500", ("A500",)),
    "932000.CSI": ("中证2000", ()),
    "899050.BJ": ("北证50", ("北证",)),
}

# 美股/ETF 扩展种子（B2）；精确 ticker 仍可 ad-hoc
US_ETF_SEED: tuple[dict[str, str], ...] = (
    {"code": "AAPL", "name": "苹果", "kind": "yfinance"},
    {"code": "MSFT", "name": "微软", "kind": "yfinance"},
    {"code": "NVDA", "name": "英伟达", "kind": "yfinance"},
    {"code": "AMD", "name": "超威半导体", "kind": "yfinance"},
    {"code": "AMZN", "name": "亚马逊", "kind": "yfinance"},
    {"code": "GOOGL", "name": "谷歌A", "kind": "yfinance"},
    {"code": "META", "name": "Meta", "kind": "yfinance"},
    {"code": "TSLA", "name": "特斯拉", "kind": "yfinance"},
    {"code": "TSM", "name": "台积电", "kind": "yfinance"},
    {"code": "ASML", "name": "阿斯麦", "kind": "yfinance"},
    {"code": "QCOM", "name": "高通", "kind": "yfinance"},
    {"code": "INTC", "name": "英特尔", "kind": "yfinance"},
    {"code": "AVGO", "name": "博通", "kind": "yfinance"},
    {"code": "MU", "name": "美光科技", "kind": "yfinance"},
    {"code": "QQQ", "name": "纳指100 ETF", "kind": "yfinance"},
    {"code": "SPY", "name": "标普500 ETF", "kind": "yfinance"},
    {"code": "IWM", "name": "罗素2000 ETF", "kind": "yfinance"},
    {"code": "KWEB", "name": "中概互联ETF", "kind": "yfinance"},
    {"code": "MCHI", "name": "MSCI中国指数ETF", "kind": "yfinance"},
    {"code": "SOXX", "name": "半导体ETF-iShares", "kind": "yfinance"},
    {"code": "SMH", "name": "半导体ETF-VanEck", "kind": "yfinance"},
    {"code": "ROBO", "name": "ROBO全球机器人", "kind": "yfinance"},
    {"code": "BOTZ", "name": "GX机器人与AI", "kind": "yfinance"},
    {"code": "BABA", "name": "阿里巴巴", "kind": "yfinance"},
    {"code": "PDD", "name": "拼多多", "kind": "yfinance"},
    {"code": "JD", "name": "京东", "kind": "yfinance"},
    {"code": "NIO", "name": "蔚来", "kind": "yfinance"},
    {"code": "XPEV", "name": "小鹏", "kind": "yfinance"},
    {"code": "LI", "name": "理想", "kind": "yfinance"},
)

HK_SEED: tuple[dict[str, str], ...] = (
    {"code": "00700.HK", "name": "腾讯控股", "kind": "hk"},
    {"code": "09988.HK", "name": "阿里巴巴-SW", "kind": "hk"},
    {"code": "03690.HK", "name": "美团-W", "kind": "hk"},
    {"code": "01810.HK", "name": "小米集团-W", "kind": "hk"},
    {"code": "09618.HK", "name": "京东集团-SW", "kind": "hk"},
    {"code": "00981.HK", "name": "中芯国际", "kind": "hk"},
    {"code": "02318.HK", "name": "中国平安", "kind": "hk"},
    {"code": "01299.HK", "name": "友邦保险", "kind": "hk"},
    {"code": "00388.HK", "name": "香港交易所", "kind": "hk"},
    {"code": "00005.HK", "name": "汇丰控股", "kind": "hk"},
)


def _state_root() -> Path:
    env = os.environ.get("KSS_STATE_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def catalog_path() -> Path:
    return (
        _state_root() / "storage" / "ui_surface"
        / f"bind_catalog_v{CATALOG_VERSION}.json"
    )


def _item(
    *,
    id: str,
    kind: str,
    market: str,
    codes: dict[str, str],
    names: list[str],
    aliases: list[str],
    allowed_slots: list[str],
    resolve_ref: str,
    status: str = "active",
    provenance: str = "",
    domains: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": id,
        "kind": kind,
        "market": market,
        "codes": codes,
        "names": names,
        "aliases": aliases,
        "allowed_slots": allowed_slots,
        "resolve_ref": resolve_ref,
        "status": status,
        "provenance": provenance,
        "domains": domains or [],
    }
    if extra:
        row.update(extra)
    return row


def _metric_items() -> list[dict[str, Any]]:
    """HOT_METRICS → strip 槽可绑项（按 metric_id 寻址）。

    kind=="index" 的几项（科创50/创业板指/上证/深证/A50）**不进 index_board**：
    strip 按 metric_id 绑、index_board 按 code 绑，是两套寻址；且 index_a50
    的 XIN9 根本不在 indexBoard 的抓取表里。index_board 的目录见
    ``_index_board_items()``。
    """
    out: list[dict[str, Any]] = []
    for mid, meta in HOT_METRICS.items():
        aliases = list(meta.get("aliases") or [])
        title = str(meta["title"])
        if title not in aliases:
            aliases = [title, *aliases]
        extra: dict[str, Any] = {"metric_id": mid}
        if meta.get("index_code"):
            extra["index_code"] = meta["index_code"]
        out.append(
            _item(
                id=f"metric.{mid}",
                kind=str(meta["kind"]),
                market=str(meta["market"]),
                codes={"metric_id": mid, **(
                    {"index_code": meta["index_code"]} if meta.get("index_code") else {}
                )},
                names=[title],
                aliases=aliases,
                allowed_slots=[SLOT_STRIP],
                resolve_ref=f"metric:{mid}",
                provenance="hot_metrics",
                domains=[str(meta.get("domain") or "metric_hot")],
                extra=extra,
            )
        )
    return out


def _index_board_items() -> list[dict[str, Any]]:
    """指数一览可绑目录：按 code 寻址，码集 = DEFAULT_INDEX_BOARD_CODES。"""
    out: list[dict[str, Any]] = []
    for raw in DEFAULT_INDEX_BOARD_CODES:
        code = str(raw).upper()
        entry = INDEX_BOARD_NAMES.get(code)
        if entry is None:
            # 新加的默认码没配名字：仍然进目录（宁可显示裸码，也不要缺项 picker），
            # 但要吵出来，并由 test_index_board_names_cover_defaults 拦在 CI。
            logger.warning("bind_catalog: index_board code %s 缺展示名，暂用裸码", code)
            name, extra = code, ()
        else:
            name, extra = entry
        bare = code.split(".")[0]
        aliases: list[str] = []
        for a in (name, *extra, code, bare, code.lower()):
            if a and a not in aliases:
                aliases.append(a)
        out.append(
            _item(
                id=f"index.{code.lower().replace('.', '_')}",
                kind="index",
                market="CN",
                codes={"code": code, "primary": code, "index_code": code},
                names=[name],
                aliases=aliases,
                allowed_slots=[SLOT_INDEX_BOARD],
                resolve_ref=f"index_board:{code}",
                provenance="index_board_default",
                domains=["index_cn"],
                extra={"code": code, "index_code": code},
            )
        )
    return out


def _aliases_for_code(code: str, name: str) -> list[str]:
    """合并展示名 + SYMBOL_ALIASES 反向别名，避免「纳指」误命中 QQQ。"""
    from kss.ui_surface.aliases import SYMBOL_ALIASES

    aliases = {name, code, code.lower()}
    cu = code.upper()
    for alias, row in SYMBOL_ALIASES.items():
        if str(row.get("code", "")).upper() == cu:
            aliases.add(alias)
            aliases.add(str(row.get("name") or ""))
    return [a for a in aliases if a]


def _overnight_symbol_items() -> list[dict[str, Any]]:
    from kss.ui_surface.resolve import candidate_overnight

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in candidate_overnight():
        code = str(row.get("code") or "").upper()
        if not code or code in seen:
            continue
        seen.add(code)
        kind = str(row.get("kind") or "yfinance")
        name = str(row.get("name") or code)
        domain = "equity_us" if kind == "yfinance" else "metric_hot"
        out.append(
            _item(
                id=f"equity.{kind}.{code.lower()}",
                kind="equity" if kind == "yfinance" else "index",
                market="US" if kind == "yfinance" else "GLOBAL",
                codes={"code": code, "primary": code},
                names=[name],
                aliases=_aliases_for_code(code, name),
                allowed_slots=[SLOT_OVERNIGHT],
                resolve_ref=f"overnight:{kind}",
                provenance="candidate_overnight",
                domains=[domain],
                extra={"code": code, "overnight_kind": kind},
            )
        )
    for row in US_ETF_SEED:
        code = row["code"].upper()
        if code in seen:
            continue
        seen.add(code)
        out.append(
            _item(
                id=f"equity.yfinance.{code.lower()}",
                kind="etf" if code in {
                    "QQQ", "SPY", "IWM", "KWEB", "MCHI", "SOXX", "SMH", "ROBO", "BOTZ",
                } else "equity",
                market="US",
                codes={"code": code, "primary": code},
                names=[row["name"]],
                aliases=_aliases_for_code(code, row["name"]),
                allowed_slots=[SLOT_OVERNIGHT],
                resolve_ref="overnight:yfinance",
                provenance="us_etf_seed",
                domains=["equity_us"],
                extra={"code": code, "overnight_kind": "yfinance"},
            )
        )
    return out


def _hk_items() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in HK_SEED:
        code = row["code"].upper()
        name = row["name"]
        out.append(
            _item(
                id=f"equity.hk.{code.lower().replace('.', '_')}",
                kind="equity",
                market="HK",
                codes={"code": code, "primary": code, "longbridge": code},
                names=[name],
                aliases=[name, code, code.replace(".HK", ""), code.lower()],
                allowed_slots=[SLOT_OVERNIGHT],
                resolve_ref="overnight:hk",
                provenance="hk_seed",
                domains=["equity_hk"],
                extra={"code": code, "overnight_kind": "hk"},
            )
        )
    return out


def _cn_items_from_name_index(limit: int = 8000) -> list[dict[str, Any]]:
    """从 stock_name_index 注入 A 股（大表；search 侧再过滤）。"""
    path = _state_root() / "storage" / "macro" / "stock_name_index.json"
    if not path.is_file():
        logger.info("bind_catalog: no stock_name_index at %s", path)
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("bind_catalog: read name index failed: %s", exc)
        return []

    # 兼容：
    # - { byName: {name: code}, byCode: {code: name}, ... }
    # - {name: code} / {code: name}
    # - list[{ts_code,name}]
    pairs: list[tuple[str, str]] = []
    if isinstance(data, dict):
        by_name = data.get("byName") if isinstance(data.get("byName"), dict) else None
        by_code = data.get("byCode") if isinstance(data.get("byCode"), dict) else None
        if by_name:
            for name, code in by_name.items():
                c = str(code).upper()
                if _TS_CODE_RE.match(c):
                    pairs.append((c, str(name)))
        if by_code:
            for code, name in by_code.items():
                c = str(code).upper()
                if _TS_CODE_RE.match(c):
                    pairs.append((c, str(name)))
        if not pairs:
            for k, v in data.items():
                if k in ("byName", "byCode", "updated_at", "version"):
                    continue
                if isinstance(v, str):
                    if _TS_CODE_RE.match(v):
                        pairs.append((v.upper(), str(k)))
                    elif _TS_CODE_RE.match(k):
                        pairs.append((k.upper(), v))
                elif isinstance(v, dict):
                    code = str(v.get("ts_code") or v.get("code") or k).upper()
                    name = str(v.get("name") or v.get("symbol") or k)
                    if _TS_CODE_RE.match(code):
                        pairs.append((code, name))
    elif isinstance(data, list):
        for row in data:
            if not isinstance(row, dict):
                continue
            code = str(row.get("ts_code") or row.get("code") or "").upper()
            name = str(row.get("name") or code)
            if _TS_CODE_RE.match(code):
                pairs.append((code, name))

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for code, name in pairs:
        if code in seen:
            continue
        seen.add(code)
        if len(out) >= limit:
            break
        bare = code.split(".")[0]
        out.append(
            _item(
                id=f"equity.cn.{code.lower().replace('.', '_')}",
                kind="equity",
                market="CN",
                codes={"code": code, "ts_code": code, "primary": code},
                names=[name],
                aliases=[name, code, bare, code.lower()],
                allowed_slots=[SLOT_OVERNIGHT],
                resolve_ref="overnight:a_share",
                provenance="stock_name_index",
                domains=["equity_cn"],
                extra={"code": code, "overnight_kind": "a_share"},
            )
        )
    return out


def build_catalog(*, include_cn: bool = True) -> dict[str, Any]:
    """构建完整 catalog 字典。"""
    items = (
        _metric_items()
        + _index_board_items()
        + _overnight_symbol_items()
        + _hk_items()
    )
    domains = {"metric_hot", "index_cn", "equity_us", "equity_hk"}
    if include_cn:
        cn = _cn_items_from_name_index()
        if cn:
            items.extend(cn)
            domains.add("equity_cn")
    # 去重 id
    by_id: dict[str, dict[str, Any]] = {}
    for it in items:
        by_id[it["id"]] = it
    ordered = list(by_id.values())
    return {
        "version": CATALOG_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "domains_online": sorted(domains),
        "item_count": len(ordered),
        "items": ordered,
    }


def save_catalog(catalog: dict[str, Any] | None = None) -> Path:
    cat = catalog if catalog is not None else build_catalog()
    path = catalog_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def load_catalog(*, rebuild_if_missing: bool = True) -> dict[str, Any]:
    path = catalog_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("items"), list):
                if data.get("version") == CATALOG_VERSION:
                    return data
                logger.info(
                    "bind_catalog: 物化版本 %s ≠ %s，重建",
                    data.get("version"), CATALOG_VERSION,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("bind_catalog: load failed %s", exc)
    if rebuild_if_missing:
        cat = build_catalog()
        try:
            save_catalog(cat)
        except Exception as exc:  # noqa: BLE001
            logger.warning("bind_catalog: save failed %s", exc)
        return cat
    degraded = _metric_items() + _index_board_items()
    return {
        "version": CATALOG_VERSION,
        "generated_at": None,
        "domains_online": ["metric_hot", "index_cn"],
        "item_count": len(degraded),
        "items": degraded,
    }


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _item_match_score(item: dict[str, Any], q: str) -> int:
    """越高越优先；0 = 不匹配。"""
    if not q:
        return 1
    qn = _norm(q)
    qu = q.strip().upper()
    codes = item.get("codes") or {}
    primary = str(codes.get("primary") or codes.get("code") or codes.get("metric_id") or "").upper()
    if primary == qu or str(codes.get("metric_id") or "") == qn:
        return 100
    aliases = [_norm(a) for a in (item.get("aliases") or [])]
    names = [_norm(n) for n in (item.get("names") or [])]
    if qn in aliases or qn in names:
        return 90
    # 整词边界：短查询「纳指」不因「纳指100」假阳性压过 IXIC
    if any(a == qn or a.startswith(qn + " ") or a.startswith(qn + "1") for a in aliases + names if a):
        return 65
    if len(qn) >= 2 and any(qn in a for a in aliases + names if a and len(a) - len(qn) <= 2):
        return 55
    if len(qn) >= 3 and any(qn in a for a in aliases + names if a):
        return 40
    if primary and (qn in primary.lower() or qu in primary):
        return 40
    return 0


def search(
    slot: str,
    q: str = "",
    *,
    market: str | None = None,
    kind: str | None = None,
    domain: str | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
    catalog: dict[str, Any] | None = None,
    status: str = "active",
) -> dict[str, Any]:
    """按槽位过滤并搜索。"""
    cat = catalog if catalog is not None else load_catalog()
    slot_n = (slot or "").strip()
    # 兼容旧 region 名
    if slot_n in ("overnight_us", "overnight", "overnight_us_marquee"):
        slot_n = SLOT_OVERNIGHT
    if slot_n in ("strip_metric_slot", "metric", "strip_slots", "strip"):
        slot_n = SLOT_STRIP
    if slot_n in ("index_board", "indices", "indexBoard"):
        slot_n = SLOT_INDEX_BOARD
    if slot_n not in (SLOT_OVERNIGHT, SLOT_STRIP, SLOT_INDEX_BOARD):
        return {
            "ok": False,
            "error": "bad_slot",
            "error_zh": (
                f"未知 slot：{slot}"
                "（可用 overnight_marquee / strip_metric / index_board）"
            ),
            "domains_online": cat.get("domains_online") or [],
            "items": [],
            "total": 0,
        }

    hits: list[tuple[int, dict[str, Any]]] = []
    for it in cat.get("items") or []:
        if not isinstance(it, dict):
            continue
        if status and it.get("status") != status:
            continue
        slots = it.get("allowed_slots") or []
        if slot_n not in slots:
            continue
        if market and str(it.get("market") or "").upper() != market.upper():
            continue
        if kind and str(it.get("kind") or "") != kind:
            continue
        if domain:
            doms = it.get("domains") or []
            if domain not in doms:
                continue
        score = _item_match_score(it, q)
        if score <= 0:
            continue
        hits.append((score, it))

    hits.sort(key=lambda x: (-x[0], str(x[1].get("names", [""])[0] if x[1].get("names") else "")))
    lim = max(1, min(int(limit or DEFAULT_SEARCH_LIMIT), 200))
    items = [h[1] for h in hits[:lim]]
    return {
        "ok": True,
        "slot": slot_n,
        "q": q,
        "domains_online": cat.get("domains_online") or [],
        "items": items,
        "total": len(hits),
    }


def resolve_overnight_from_catalog_item(item: dict[str, Any]) -> dict[str, str]:
    """catalog equity 项 → overnight code/kind/name。"""
    codes = item.get("codes") or {}
    code = str(codes.get("primary") or codes.get("code") or item.get("code") or "").upper()
    kind = str(item.get("overnight_kind") or "yfinance")
    names = item.get("names") or []
    name = str(names[0] if names else code)
    return {"code": code, "kind": kind, "name": name}


def resolve_metric_id_from_catalog_item(item: dict[str, Any]) -> str | None:
    mid = item.get("metric_id")
    if mid:
        return str(mid)
    codes = item.get("codes") or {}
    if codes.get("metric_id"):
        return str(codes["metric_id"])
    rid = str(item.get("resolve_ref") or "")
    if rid.startswith("metric:"):
        return rid.split(":", 1)[1]
    return None


def guess_overnight_kind(code: str) -> str:
    c = (code or "").strip().upper()
    if _TS_CODE_RE.match(c):
        return "a_share"
    if _HK_CODE_RE.match(c):
        return "hk"
    return "yfinance"
