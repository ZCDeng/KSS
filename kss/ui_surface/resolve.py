"""Surface resolve：effective overnight universe、指标目录、候选表、预览 props。"""

from __future__ import annotations

from typing import Any

from kss.ui_surface.config import (
    DEFAULT_STRIP_METRIC,
    DEFAULT_STRIP_SLOTS,
    STRIP_SLOT_IDS,
    default_strip_slots,
    effective_index_board_codes,
    load_config,
)


def _overnight_defaults() -> list[dict[str, Any]]:
    from scripts.overnight_us_universe import OVERNIGHT_US_UNIVERSE

    return [
        {
            "code": r["code"],
            "name": r["name"],
            "kind": r["kind"],
            "yfinance_symbol": r["code"] if r["kind"] == "yfinance" else None,
            "longbridge_symbol": None,
        }
        for r in OVERNIGHT_US_UNIVERSE
    ]


# 候选表 = 默认 12 + 常用扩展（+ 菜单只列这些）
_EXTRA_CANDIDATES: tuple[dict[str, str], ...] = (
    {"code": "AAPL", "name": "苹果", "kind": "yfinance"},
    {"code": "MSFT", "name": "微软", "kind": "yfinance"},
    {"code": "AMD", "name": "超威半导体", "kind": "yfinance"},
    {"code": "AMZN", "name": "亚马逊", "kind": "yfinance"},
    {"code": "GOOGL", "name": "谷歌A", "kind": "yfinance"},
    {"code": "META", "name": "Meta", "kind": "yfinance"},
    {"code": "TSM", "name": "台积电", "kind": "yfinance"},
    {"code": "ASML", "name": "阿斯麦", "kind": "yfinance"},
    {"code": "QCOM", "name": "高通", "kind": "yfinance"},
    {"code": "INTC", "name": "英特尔", "kind": "yfinance"},
    {"code": "QQQ", "name": "纳指100 ETF", "kind": "yfinance"},
    {"code": "SPY", "name": "标普500 ETF", "kind": "yfinance"},
    {"code": "IWM", "name": "罗素2000 ETF", "kind": "yfinance"},
    {"code": "KWEB", "name": "中概互联ETF", "kind": "yfinance"},
)


def candidate_overnight() -> list[dict[str, Any]]:
    """候选表：默认段与 OVERNIGHT_US_UNIVERSE 一致 + 扩展。"""
    base = _overnight_defaults()
    seen = {r["code"].upper() for r in base}
    out = list(base)
    for row in _EXTRA_CANDIDATES:
        code = row["code"].upper()
        if code in seen:
            continue
        seen.add(code)
        out.append({
            "code": code,
            "name": row["name"],
            "kind": row["kind"],
            "yfinance_symbol": code,
            "longbridge_symbol": None,
        })
    return out


# 模块加载时固定一份供测试比对（默认段引用函数再算）
CANDIDATE_OVERNIGHT = candidate_overnight()

METRIC_CATALOG: dict[str, dict[str, Any]] = {
    "limit_max_board": {
        "metric_id": "limit_max_board",
        "title": "最高连板",
        "description": "涨停板最高连板高度（limit_list_d）",
        "unit": "板",
    },
    "limit_seal_rate": {
        "metric_id": "limit_seal_rate",
        "title": "封板率",
        "description": "涨停封板 / (封板+曾开板) 近似",
        "unit": "%",
    },
    "limit_up_count": {
        "metric_id": "limit_up_count",
        "title": "涨停家数",
        "description": "涨停家数（limitBoard.total）",
        "unit": "家",
    },
    "limit_break_rate": {
        "metric_id": "limit_break_rate",
        "title": "破板率",
        "description": "涨停破板率（limitBoard.breakRate）",
        "unit": "%",
    },
    "index_kcb50": {
        "metric_id": "index_kcb50",
        "title": "科创50",
        "description": "指数一览中的科创50 收盘与涨跌",
        "unit": "",
        "index_code": "000688.SH",
    },
    "index_cyb": {
        "metric_id": "index_cyb",
        "title": "创业板指",
        "description": "指数一览中的创业板指 收盘与涨跌",
        "unit": "",
        "index_code": "399006.SZ",
    },
    "index_sse": {
        "metric_id": "index_sse",
        "title": "上证指数",
        "description": "上证指数 收盘与涨跌",
        "unit": "",
        "index_code": "000001.SH",
    },
    "index_szse": {
        "metric_id": "index_szse",
        "title": "深证成指",
        "description": "深证成指 收盘与涨跌",
        "unit": "",
        "index_code": "399001.SZ",
    },
    "index_a50": {
        "metric_id": "index_a50",
        "title": "富时中国A50",
        "description": "富时中国A50（XIN9）",
        "unit": "",
        "index_code": "XIN9",
    },
    "north_money": {
        "metric_id": "north_money",
        "title": "北向资金",
        "description": "沪深港通北向净流入（亿元）",
        "unit": "亿",
    },
    "etf_a500_563360": {
        "metric_id": "etf_a500_563360",
        "title": "A500ETF",
        "description": "A500ETF 563360.SH",
        "unit": "",
        "etf_code": "563360.SH",
    },
    "etf_a500_159361": {
        "metric_id": "etf_a500_159361",
        "title": "A500ETF",
        "description": "A500ETF 159361.SZ",
        "unit": "",
        "etf_code": "159361.SZ",
    },
}


def effective_overnight_universe(
    append: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """默认 universe 行 + 用户 append 行（有序）。"""
    from scripts.overnight_us_universe import OVERNIGHT_US_UNIVERSE

    out: list[dict[str, Any]] = [
        {"code": r["code"], "name": r["name"], "kind": r["kind"]}
        for r in OVERNIGHT_US_UNIVERSE
    ]
    seen = {r["code"].upper() for r in out}
    for item in append or []:
        code = str(item.get("code", "")).upper()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append({
            "code": code,
            "name": str(item.get("name") or code),
            "kind": str(item.get("kind") or "yfinance"),
        })
    return out


def probe_overnight_code(code: str, kind: str | None = None) -> dict[str, Any]:
    """轻量报价探针：成功返回 close/pct/kind；失败 ok=False。

    不落盘。用于 AI 表外码写入前校验。
    kind: yfinance | index_global | a_share | hk
    """
    from kss.ui_surface.bind_catalog import guess_overnight_kind
    from kss.ui_surface.config import is_valid_overnight_code

    raw = (code or "").strip().upper()
    kind_in = (kind or "").strip().lower() or None
    if not raw:
        return {"ok": False, "error": "invalid_code", "code": raw}
    if kind_in is None:
        kind_in = guess_overnight_kind(raw)
    if not is_valid_overnight_code(raw, kind_in):
        return {"ok": False, "error": "invalid_code", "code": raw}

    # 候选表命中
    name = raw
    for row in candidate_overnight():
        if row["code"].upper() == raw:
            kind_in = row["kind"]
            name = row["name"]
            break
    kind = kind_in

    try:
        if kind == "index_global":
            close, pct, date = _probe_index_global(raw)
            source = "index_global"
        elif kind == "a_share":
            close, pct, date, source = _probe_a_share(raw)
        elif kind == "hk":
            close, pct, date, source = _probe_hk(raw)
        else:
            close, pct, date = _probe_yfinance(raw)
            source = "yfinance"
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"probe_failed: {exc}",
            "code": raw,
            "kind": kind,
        }
    if close is None or pct is None:
        return {
            "ok": False,
            "error": "no_quote",
            "code": raw,
            "kind": kind,
        }
    return {
        "ok": True,
        "code": raw,
        "name": name,
        "kind": kind,
        "close": close,
        "pct": pct,
        "date": date or "",
        "source": source,
    }


def _probe_a_share(code: str) -> tuple[float | None, float | None, str, str]:
    """A 股：longbridge 优先，否则日线 parquet/cs 不强制；失败 (None,None,'',src)。"""
    # Longbridge 产品码：600519.SH → 600519.SH 或 SH600519 视 ro 而定
    try:
        from kss.longbridge import ro as lb_ro  # type: ignore

        quote = None
        if hasattr(lb_ro, "get_quote"):
            quote = lb_ro.get_quote(code)
        elif hasattr(lb_ro, "quote"):
            quote = lb_ro.quote(code)
        if isinstance(quote, dict):
            last = quote.get("last") or quote.get("close") or quote.get("price")
            prev = quote.get("prev_close") or quote.get("prevClose")
            if last is not None:
                last_f = float(last)
                pct = 0.0
                if prev:
                    pct = (last_f / float(prev) - 1.0) * 100.0
                elif quote.get("pct") is not None:
                    pct = float(quote["pct"])
                return round(last_f, 4), round(pct, 2), "", "longbridge"
    except Exception:  # noqa: BLE001
        pass
    # 日线简单 fallback：tushare daily 最新
    try:
        from kss.data.tushare_client import TushareClient, _fetch_with_retry

        pro = TushareClient().get_pro()
        df = _fetch_with_retry(
            lambda: pro.daily(ts_code=code, limit=5),
            f"daily {code}",
        )
        if df is not None and not df.empty:
            df = df.sort_values("trade_date")
            r = df.iloc[-1]
            close = float(r["close"])
            pct = float(r["pct_chg"]) if r.get("pct_chg") is not None else 0.0
            return round(close, 4), round(pct, 2), str(r["trade_date"]), "tushare_daily"
    except Exception:  # noqa: BLE001
        pass
    return None, None, "", "a_share"


def _probe_hk(code: str) -> tuple[float | None, float | None, str, str]:
    try:
        from kss.longbridge import ro as lb_ro  # type: ignore

        quote = None
        if hasattr(lb_ro, "get_quote"):
            quote = lb_ro.get_quote(code)
        elif hasattr(lb_ro, "quote"):
            quote = lb_ro.quote(code)
        if isinstance(quote, dict):
            last = quote.get("last") or quote.get("close") or quote.get("price")
            prev = quote.get("prev_close") or quote.get("prevClose")
            if last is not None:
                last_f = float(last)
                pct = 0.0
                if prev:
                    pct = (last_f / float(prev) - 1.0) * 100.0
                elif quote.get("pct") is not None:
                    pct = float(quote["pct"])
                return round(last_f, 4), round(pct, 2), "", "longbridge"
    except Exception:  # noqa: BLE001
        pass
    return None, None, "", "hk"


def _probe_yfinance(code: str) -> tuple[float | None, float | None, str]:
    import yfinance as yf

    t = yf.Ticker(code)
    hist = t.history(period="5d")
    if hist is None or hist.empty:
        return None, None, ""
    last = hist.iloc[-1]
    close = float(last["Close"])
    if len(hist) >= 2:
        prev = float(hist.iloc[-2]["Close"])
        pct = (close / prev - 1.0) * 100.0 if prev else 0.0
    else:
        pct = 0.0
    date = str(hist.index[-1].date()).replace("-", "")
    return round(close, 4), round(pct, 2), date


def _probe_index_global(code: str) -> tuple[float | None, float | None, str]:
    from kss.data.tushare_client import TushareClient, _fetch_with_retry

    pro = TushareClient().get_pro()
    df = _fetch_with_retry(
        lambda: pro.index_global(ts_code=code, start_date="20260101", end_date="20261231"),
        f"index_global {code}",
    )
    if df is None or df.empty:
        return None, None, ""
    df = df.sort_values("trade_date")
    r = df.iloc[-1]
    return round(float(r["close"]), 2), round(float(r["pct_chg"]), 2), str(r["trade_date"])


def resolve_overnight_preview(
    market_strip: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """默认+追加 的展示预览：有价用 strip，无价标 pending。"""
    cfg = config if config is not None else load_config()
    append = list((cfg.get("overnight_us") or {}).get("append") or [])
    append_by_code = {str(a.get("code", "")).upper(): a for a in append}
    quoted: dict[str, dict[str, Any]] = {}
    for row in (market_strip or {}).get("overnightUS") or []:
        if isinstance(row, dict) and row.get("code"):
            quoted[str(row["code"]).upper()] = row

    out: list[dict[str, Any]] = []
    for row in effective_overnight_universe(append):
        code = row["code"].upper()
        hit = quoted.get(code)
        user = append_by_code.get(code)
        is_user = user is not None
        if hit and hit.get("close") is not None and hit.get("pct") is not None:
            out.append({
                "code": code,
                "name": hit.get("name") or row["name"],
                "close": hit["close"],
                "pct": hit["pct"],
                "date": hit.get("date") or "",
                "source": hit.get("source") or row.get("kind"),
                "pending": False,
                "isUserAppended": is_user,
                "kind_source": (user or {}).get("kind_source"),
                "probe_close": (user or {}).get("probe_close"),
            })
        elif is_user:
            out.append({
                "code": code,
                "name": row["name"],
                "close": None,
                "pct": None,
                "date": "",
                "source": row.get("kind"),
                "pending": True,
                "isUserAppended": True,
                "kind_source": user.get("kind_source"),
                "probe_close": user.get("probe_close"),
            })
        # 默认项无价：与现逻辑一致，不展示
    return out


def resolve_metric_props(
    market_strip: dict[str, Any] | None,
    metric_id: str | None = None,
) -> dict[str, Any]:
    """指标小卡 props；无数据时 value=null + reason。"""
    mid = metric_id or DEFAULT_STRIP_METRIC
    meta = METRIC_CATALOG.get(mid)
    if not meta:
        return {
            "metric_id": mid,
            "title": mid,
            "value": None,
            "valueText": "—",
            "delta": None,
            "deltaText": "",
            "sub": "未知指标",
            "reason": "unknown_metric",
        }
    strip = market_strip or {}
    title = meta["title"]

    if mid == "limit_max_board":
        lb = strip.get("limitBoard") or {}
        max_board = lb.get("maxBoard")
        if max_board is None:
            return _empty_metric(mid, title, "no_limit_board")
        return {
            "metric_id": mid,
            "title": title,
            "value": float(max_board),
            "valueText": f"{int(max_board)} 板",
            "delta": float(max_board),
            "deltaText": "最高连板",
            "sub": "涨停情绪",
            "reason": None,
        }

    if mid == "limit_seal_rate":
        lb = strip.get("limitBoard") or {}
        rate = lb.get("sealRate")
        if rate is None:
            return _empty_metric(mid, title, "no_seal_rate")
        pct = float(rate) * 100.0 if float(rate) <= 1.0 else float(rate)
        return {
            "metric_id": mid,
            "title": title,
            "value": pct,
            "valueText": f"{pct:.1f}%",
            "delta": pct,
            "deltaText": "封板率",
            "sub": "涨停情绪",
            "reason": None,
        }

    if mid == "limit_up_count":
        lb = strip.get("limitBoard") or {}
        total = lb.get("total")
        if total is None:
            return _empty_metric(mid, title, "no_limit_total")
        return {
            "metric_id": mid,
            "title": title,
            "value": float(total),
            "valueText": f"{int(total)} 家",
            "delta": float(total),
            "deltaText": "涨停",
            "sub": "涨停情绪",
            "reason": None,
        }

    if mid == "limit_break_rate":
        lb = strip.get("limitBoard") or {}
        rate = lb.get("breakRate")
        if rate is None:
            return _empty_metric(mid, title, "no_break_rate")
        pct = float(rate) * 100.0 if float(rate) <= 1.0 else float(rate)
        return {
            "metric_id": mid,
            "title": title,
            "value": pct,
            "valueText": f"{pct:.1f}%",
            "delta": pct,
            "deltaText": "破板率",
            "sub": "涨停情绪",
            "reason": None,
        }

    if mid == "north_money":
        nm = strip.get("northMoney")
        if nm is None:
            return _empty_metric(mid, title, "no_north_money")
        yi = float(nm) / 10000.0  # 万元 → 亿
        return {
            "metric_id": mid,
            "title": title,
            "value": yi,
            "valueText": f"{yi:+.1f} 亿",
            "delta": yi,
            "deltaText": "净流入" if yi >= 0 else "净流出",
            "sub": "沪深港通",
            "reason": None,
        }

    etf_code = meta.get("etf_code")
    if etf_code:
        found = _find_etf_quote(strip, etf_code)
        if found:
            close, pct = found
            return {
                "metric_id": mid,
                "title": title,
                "value": float(close),
                "valueText": f"{float(close):.3f}",
                "delta": float(pct) if pct is not None else None,
                "deltaText": (
                    f"{float(pct):+.2f}%" if pct is not None else ""
                ),
                "sub": etf_code,
                "reason": None,
            }
        return _empty_metric(mid, title, "etf_not_in_strip")

    index_code = meta.get("index_code")
    if index_code:
        found = _find_index_quote(strip, index_code)
        if found:
            close, pct = found
            return {
                "metric_id": mid,
                "title": title,
                "value": float(close),
                "valueText": f"{float(close):.2f}",
                "delta": float(pct) if pct is not None else None,
                "deltaText": (
                    f"{float(pct):+.2f}%" if pct is not None else ""
                ),
                "sub": index_code,
                "reason": None,
            }
        # A50 等可能只在 overnightUS
        if index_code.upper() == "XIN9":
            for row in strip.get("overnightUS") or []:
                if str(row.get("code", "")).upper() == "XIN9":
                    close = row.get("close")
                    pct = row.get("pct")
                    if close is not None:
                        return {
                            "metric_id": mid,
                            "title": title,
                            "value": float(close),
                            "valueText": f"{float(close):.2f}",
                            "delta": float(pct) if pct is not None else None,
                            "deltaText": (
                                f"{float(pct):+.2f}%" if pct is not None else ""
                            ),
                            "sub": "XIN9",
                            "reason": None,
                        }
        return _empty_metric(mid, title, "index_not_in_board")

    return _empty_metric(mid, title, "unresolved")


def resolve_strip_slots(
    market_strip: dict[str, Any] | None,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """四槽 props；每槽独立 resolve。"""
    cfg = config if config is not None else load_config()
    slots = cfg.get("strip_slots") or default_strip_slots()
    out: list[dict[str, Any]] = []
    for i, sid in enumerate(STRIP_SLOT_IDS):
        mid = DEFAULT_STRIP_SLOTS[i] if i >= len(DEFAULT_STRIP_SLOTS) else DEFAULT_STRIP_SLOTS[i]
        if i < len(slots) and isinstance(slots[i], dict):
            mid = str(slots[i].get("metric_id") or mid)
        props = resolve_metric_props(market_strip, mid)
        props["slot_id"] = sid
        out.append(props)
    return out


def effective_index_board_quotes(
    market_strip: dict[str, Any] | None,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """按 effective codes 从 strip 取指数板展示行；缺价仍保留 code/name 骨架。"""
    codes = effective_index_board_codes(config)
    strip = market_strip or {}
    by_code: dict[str, dict[str, Any]] = {}
    for board_key in ("indexBoard", "indices"):
        for idx in strip.get(board_key) or []:
            c = str(idx.get("code", "")).upper()
            if c:
                by_code[c] = idx
    out: list[dict[str, Any]] = []
    for code in codes:
        row = by_code.get(code.upper())
        if row:
            out.append({
                "code": code,
                "name": row.get("name") or code,
                "close": row.get("close"),
                "pct": row.get("pct"),
                "date": row.get("date"),
            })
        else:
            out.append({
                "code": code,
                "name": code,
                "close": None,
                "pct": None,
                "date": None,
            })
    return out


def _find_etf_quote(
    strip: dict[str, Any], etf_code: str
) -> tuple[float, float | None] | None:
    code_u = etf_code.upper()
    for etf in strip.get("etfs") or []:
        if str(etf.get("code", "")).upper() == code_u:
            close = etf.get("close")
            if close is None:
                return None
            return float(close), (
                float(etf["pct"]) if etf.get("pct") is not None else None
            )
    return None


def _find_index_quote(
    strip: dict[str, Any], index_code: str
) -> tuple[float, float | None] | None:
    code_u = index_code.upper()
    for board_key in ("indexBoard", "indices"):
        for idx in strip.get(board_key) or []:
            if str(idx.get("code", "")).upper() == code_u:
                close = idx.get("close")
                if close is None:
                    return None
                return float(close), (
                    float(idx["pct"]) if idx.get("pct") is not None else None
                )
    for stack in strip.get("indexStacks") or []:
        for idx in (stack.get("items") or []):
            if str(idx.get("code", "")).upper() == code_u:
                close = idx.get("close")
                if close is None:
                    return None
                return float(close), (
                    float(idx["pct"]) if idx.get("pct") is not None else None
                )
    return None


def _empty_metric(metric_id: str, title: str, reason: str) -> dict[str, Any]:
    return {
        "metric_id": metric_id,
        "title": title,
        "value": None,
        "valueText": "—",
        "delta": None,
        "deltaText": "",
        "sub": reason,
        "reason": reason,
    }


def list_metrics_public() -> list[dict[str, Any]]:
    return [
        {
            "metric_id": m["metric_id"],
            "title": m["title"],
            "description": m["description"],
        }
        for m in METRIC_CATALOG.values()
    ]
