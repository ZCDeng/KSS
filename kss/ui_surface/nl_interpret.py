"""档 A 确定性 NL 解析：utterance + region → draft（不落盘）。

探针通过 probe_fn 注入，便于单测。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from kss.ui_surface.aliases import (
    METRIC_SUGGESTIONS,
    build_symbol_alias_index_from_candidates,
    is_north_utterance,
    lookup_metric,
    lookup_symbol,
)
from kss.ui_surface.config import (
    CODE_RE,
    DEFAULT_INDEX_BOARD_CODES,
    STRIP_SLOT_IDS,
    default_codes,
    effective_index_board_codes,
    load_config,
)
from kss.ui_surface.resolve import (
    METRIC_CATALOG,
    candidate_overnight,
    resolve_metric_props,
)

ProbeFn = Callable[[str, str | None], dict[str, Any]]

_APPEND_VERBS = (
    "加上", "添加", "加入", "增加", "追加", "放入", "放进", "加一下", "加个", "加",
)
_REMOVE_VERBS = (
    "去掉", "删除", "移除", "删掉", "拿掉", "剔除", "取消",
)
_CLEAR_PHRASES = (
    "清空我的", "清空追加", "清空用户", "重置追加", "清除我的",
)
_METRIC_VERBS = (
    "改成", "换成", "切换到", "切换成", "设为", "设置为", "显示", "用", "改为",
)


def _split_entities(fragment: str) -> list[str]:
    """按 和/与/及/、/，/,/空格 切实体。"""
    if not fragment or not fragment.strip():
        return []
    s = fragment.strip()
    for ch in ("，", ",", "、", "；", ";", "/", "|"):
        s = s.replace(ch, " ")
    s = re.sub(r"\s+和\s+", " ", s)
    s = re.sub(r"\s+与\s+", " ", s)
    s = re.sub(r"\s+及\s+", " ", s)
    s = s.replace("和", " ").replace("与", " ").replace("及", " ")
    parts = [p.strip() for p in s.split() if p.strip()]
    return parts


def _strip_leading_verbs(text: str, verbs: tuple[str, ...]) -> tuple[str | None, str]:
    t = (text or "").strip()
    # 长动词优先
    for v in sorted(verbs, key=len, reverse=True):
        if t.startswith(v):
            return v, t[len(v):].strip()
    return None, t


def _detect_overnight_action(text: str) -> tuple[str, str]:
    """返回 (action, remainder)。action ∈ append|remove|clear_mine|unknown"""
    t = (text or "").strip()
    for p in _CLEAR_PHRASES:
        if p in t:
            return "clear_mine", ""
    # remove 优先（「去掉苹果」）
    verb, rest = _strip_leading_verbs(t, _REMOVE_VERBS)
    if verb:
        return "remove", rest
    verb, rest = _strip_leading_verbs(t, _APPEND_VERBS)
    if verb:
        return "append", rest
    # 无动词但有实体 → 默认 append
    if t:
        return "append", t
    return "unknown", t


def _resolve_token(
    token: str,
    alias_index: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """token → {status, code?, name?, kind?, error?}；优先 Bind Catalog。"""
    from kss.ui_surface.bind_catalog import (
        SLOT_OVERNIGHT,
        guess_overnight_kind,
        resolve_overnight_from_catalog_item,
        search as catalog_search,
    )

    raw = token.strip()
    if not raw:
        return {"status": "failed", "token": token, "error": "empty_token"}

    # 1) 静态别名表优先（档 A 黄金句 / 避免「纳指」子串命中 QQQ）
    key_lower = raw.lower()
    hit = alias_index.get(key_lower) or alias_index.get(raw) or lookup_symbol(raw)
    if hit:
        return {
            "status": "ok",
            "token": token,
            "code": hit["code"].upper(),
            "name": hit.get("name") or hit["code"],
            "kind": hit.get("kind") or "yfinance",
        }

    # 2) catalog 检索（overnight 槽）
    cat_hit = catalog_search(SLOT_OVERNIGHT, raw, limit=5)
    if cat_hit.get("ok") and cat_hit.get("items"):
        item = cat_hit["items"][0]
        resolved = resolve_overnight_from_catalog_item(item)
        if resolved.get("code"):
            return {
                "status": "ok",
                "token": token,
                "code": resolved["code"].upper(),
                "name": resolved.get("name") or resolved["code"],
                "kind": resolved.get("kind") or "yfinance",
                "catalog_id": item.get("id"),
            }

    # 3) 精确 code ad-hoc（probe 由上层决定成败）
    code = raw.upper()
    if CODE_RE.match(code):
        kind = guess_overnight_kind(code)
        return {
            "status": "ok",
            "token": token,
            "code": code,
            "name": code,
            "kind": kind,
        }

    domains = cat_hit.get("domains_online") or []
    domain_hint = f" 已上线域：{', '.join(domains)}" if domains else ""
    return {
        "status": "failed",
        "token": token,
        "error": "unknown_symbol",
        "error_zh": f"无法识别「{raw}」{domain_hint}。可说：苹果、茅台、AAPL、00700.HK",
    }


def interpret_overnight(
    text: str,
    *,
    config: dict[str, Any] | None = None,
    probe_fn: ProbeFn | None = None,
) -> dict[str, Any]:
    """解析隔夜 NL。"""
    from kss.ui_surface.resolve import probe_overnight_code

    cfg = config if config is not None else load_config()
    append = list((cfg.get("overnight_us") or {}).get("append") or [])
    append_by_code = {str(a.get("code", "")).upper(): a for a in append}
    defaults = default_codes()
    alias_index = build_symbol_alias_index_from_candidates(candidate_overnight())
    probe = probe_fn or probe_overnight_code

    if not (text or "").strip():
        return {
            "ok": False,
            "region": "overnight_us",
            "action": None,
            "error": "empty_text",
            "error_zh": "请输入内容，例如：加上苹果和阿斯麦",
            "suggestions": ["加上苹果", "去掉苹果", "清空我的追加"],
            "items": [],
            "previews": [],
            "ops": [],
        }

    action, remainder = _detect_overnight_action(text)

    if action == "clear_mine":
        if not append:
            return {
                "ok": False,
                "region": "overnight_us",
                "action": "reset_overnight_append",
                "error": "nothing_to_clear",
                "error_zh": "没有用户追加项可清空",
                "items": [],
                "previews": [],
                "ops": [],
            }
        ops = [{"op": "reset_overnight_append"}]
        previews = [
            {
                "op": "reset_overnight_append",
                "label": f"清空 {len(append)} 个用户追加项",
                "codes": [a.get("code") for a in append],
            }
        ]
        return {
            "ok": True,
            "region": "overnight_us",
            "action": "reset_overnight_append",
            "items": [],
            "previews": previews,
            "ops": ops,
            "ambiguities": [],
            "error": None,
            "suggestions": [],
        }

    tokens = _split_entities(remainder)
    if not tokens:
        return {
            "ok": False,
            "region": "overnight_us",
            "action": action,
            "error": "no_entities",
            "error_zh": "未识别到标的，例如：加上苹果和阿斯麦",
            "suggestions": ["加上苹果", "加上 AAPL 和 ASML"],
            "items": [],
            "previews": [],
            "ops": [],
        }

    items: list[dict[str, Any]] = []
    for tok in tokens:
        resolved = _resolve_token(tok, alias_index)
        if resolved["status"] != "ok":
            items.append(resolved)
            continue
        code = resolved["code"]
        if action == "append":
            if code in defaults:
                items.append({
                    **resolved,
                    "status": "failed",
                    "error": "is_default",
                    "error_zh": f"「{resolved['name']}」({code}) 已是系统默认项，不能追加",
                })
                continue
            if code in append_by_code:
                items.append({
                    **resolved,
                    "status": "failed",
                    "error": "already_appended",
                    "error_zh": f"「{resolved['name']}」已在用户追加中",
                })
                continue
            probe_res = probe(code, resolved.get("kind"))
            if not probe_res.get("ok"):
                items.append({
                    **resolved,
                    "status": "failed",
                    "error": probe_res.get("error") or "probe_failed",
                    "error_zh": f"无法取得「{resolved['name']}」报价：{probe_res.get('error')}",
                })
                continue
            items.append({
                **resolved,
                "status": "ok",
                "name": probe_res.get("name") or resolved["name"],
                "kind": probe_res.get("kind") or resolved["kind"],
                "close": probe_res.get("close"),
                "pct": probe_res.get("pct"),
                "date": probe_res.get("date") or "",
                "source": probe_res.get("source"),
            })
        else:  # remove
            if code in defaults:
                items.append({
                    **resolved,
                    "status": "failed",
                    "error": "cannot_remove_default",
                    "error_zh": f"「{resolved['name']}」是系统默认项，不能删除",
                })
                continue
            if code not in append_by_code:
                items.append({
                    **resolved,
                    "status": "failed",
                    "error": "not_in_append",
                    "error_zh": f"「{resolved['name']}」不在用户追加列表中",
                })
                continue
            meta = append_by_code[code]
            items.append({
                **resolved,
                "status": "ok",
                "name": meta.get("name") or resolved["name"],
                "close": meta.get("probe_close"),
                "pct": None,
            })

    ok_items = [i for i in items if i.get("status") == "ok"]
    failed = [i for i in items if i.get("status") != "ok"]
    ops: list[dict[str, Any]] = []
    previews: list[dict[str, Any]] = []
    if action == "append":
        for i in ok_items:
            ops.append({
                "op": "overnight_append",
                "code": i["code"],
                "name": i.get("name"),
                "kind": i.get("kind") or "yfinance",
                "kind_source": "candidate_table",
                "added_via": "nl",
                "probe_close": i.get("close"),
            })
            previews.append({
                "op": "overnight_append",
                "code": i["code"],
                "name": i.get("name"),
                "close": i.get("close"),
                "pct": i.get("pct"),
                "label": f"追加 {i.get('name')} ({i['code']})",
            })
    else:
        for i in ok_items:
            ops.append({"op": "overnight_remove", "code": i["code"]})
            previews.append({
                "op": "overnight_remove",
                "code": i["code"],
                "name": i.get("name"),
                "label": f"移除 {i.get('name')} ({i['code']})",
            })

    if not ok_items:
        return {
            "ok": False,
            "region": "overnight_us",
            "action": action,
            "error": "all_failed",
            "error_zh": failed[0].get("error_zh") if failed else "无法完成",
            "items": items,
            "previews": [],
            "ops": [],
            "ambiguities": [],
            "suggestions": ["加上苹果", "去掉苹果", "加上 AAPL 和 NVDA"],
        }

    return {
        "ok": True,
        "region": "overnight_us",
        "action": "overnight_append" if action == "append" else "overnight_remove",
        "items": items,
        "previews": previews,
        "ops": ops,
        "partial": bool(failed),
        "failed": failed,
        "ambiguities": [],
        "error": None,
        "suggestions": [],
    }


_SLOT_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"第\s*([1-4一二三四])\s*张"), 0),
    (re.compile(r"第\s*([1-4一二三四])\s*个"), 0),
    (re.compile(r"槽\s*([1-4])"), 0),
    (re.compile(r"slot\s*([0-3])", re.I), 1),  # 0-based slot id index
)


def _cn_digit(ch: str) -> int | None:
    m = {"1": 1, "2": 2, "3": 3, "4": 4, "一": 1, "二": 2, "三": 3, "四": 4}
    return m.get(ch)


def parse_slot_id(text: str) -> str | None:
    """从句中解析 strip 槽位；返回 strip_0..strip_3 或 None。"""
    t = text or ""
    for pat, mode in _SLOT_PATTERNS:
        m = pat.search(t)
        if not m:
            continue
        raw = m.group(1)
        if mode == 1:
            idx = int(raw)
            if 0 <= idx <= 3:
                return STRIP_SLOT_IDS[idx]
            continue
        n = _cn_digit(raw)
        if n is not None and 1 <= n <= 4:
            return STRIP_SLOT_IDS[n - 1]
    return None


def interpret_strip_metric(
    text: str,
    *,
    market_strip: dict[str, Any] | None = None,
    slot_id: str | None = None,
) -> dict[str, Any]:
    """解析四槽指标 NL；可句内点名槽位，或传入 slot_id。"""
    t = (text or "").strip()
    if not t:
        return {
            "ok": False,
            "region": "strip_metric",
            "action": None,
            "error": "empty_text",
            "error_zh": "请输入指标名，例如：第二张改成封板率",
            "suggestions": list(METRIC_SUGGESTIONS),
            "previews": [],
            "ops": [],
        }

    resolved_slot = (slot_id or "").strip() or parse_slot_id(t)
    if resolved_slot and resolved_slot not in STRIP_SLOT_IDS:
        return {
            "ok": False,
            "region": "strip_metric",
            "error": "bad_slot",
            "error_zh": f"无效槽位：{resolved_slot}（可用 strip_0..strip_3）",
            "previews": [],
            "ops": [],
        }

    # 去掉动词前缀与槽位短语
    work = t
    for pat, _ in _SLOT_PATTERNS:
        work = pat.sub(" ", work)
    _, rest = _strip_leading_verbs(work, _METRIC_VERBS)
    rest = rest.strip()
    for noise in ("指标", "小卡", "显示", "一下", "改成", "改为", "换成", "第张", "个"):
        rest = rest.replace(noise, " ")
    rest = rest.strip() or work.strip() or t

    # 北向：四槽模型允许绑 north_money
    if is_north_utterance(t) and "五日" not in t and "5日" not in t:
        metric_id = "north_money"
    else:
        from kss.ui_surface.bind_catalog import (
            SLOT_STRIP,
            resolve_metric_id_from_catalog_item,
            search as catalog_search,
        )

        metric_id = lookup_metric(rest) or lookup_metric(t)
        if not metric_id:
            cat = catalog_search(SLOT_STRIP, rest or t, limit=5)
            domains = cat.get("domains_online") or []
            if cat.get("ok") and cat.get("items"):
                metric_id = resolve_metric_id_from_catalog_item(cat["items"][0])
            if not metric_id:
                from kss.ui_surface.aliases import METRIC_ALIASES

                for key, mid in sorted(METRIC_ALIASES.items(), key=lambda x: -len(x[0])):
                    if key in t or key in rest:
                        metric_id = mid
                        break
            if not metric_id or metric_id not in METRIC_CATALOG:
                domain_hint = f" 已上线域：{', '.join(domains)}" if domains else ""
                return {
                    "ok": False,
                    "region": "strip_metric",
                    "action": "set_strip_slot",
                    "error": "unknown_metric",
                    "error_zh": (
                        f"暂不支持「{rest or t}」{domain_hint}。"
                        "可说：" + "、".join(METRIC_SUGGESTIONS)
                    ),
                    "suggestions": list(METRIC_SUGGESTIONS),
                    "domains_online": domains,
                    "previews": [],
                    "ops": [],
                }
        if metric_id not in METRIC_CATALOG:
            return {
                "ok": False,
                "region": "strip_metric",
                "action": "set_strip_slot",
                "error": "unknown_metric",
                "error_zh": (
                    f"暂不支持「{rest or t}」。可说："
                    + "、".join(METRIC_SUGGESTIONS)
                ),
                "suggestions": list(METRIC_SUGGESTIONS),
                "previews": [],
                "ops": [],
            }

    if not resolved_slot:
        return {
            "ok": False,
            "region": "strip_metric",
            "action": "set_strip_slot",
            "metric_id": metric_id,
            "error": "slot_required",
            "error_zh": (
                "请先选槽，或说「第二张改成封板率」。"
                f"已识别指标：{METRIC_CATALOG.get(metric_id, {}).get('title', metric_id)}"
            ),
            "suggestions": ["第一张改成封板率", "第二张改成北向资金"],
            "previews": [],
            "ops": [],
        }

    props = resolve_metric_props(market_strip, metric_id)
    props["slot_id"] = resolved_slot
    ops = [{
        "op": "set_strip_slot",
        "slot_id": resolved_slot,
        "metric_id": metric_id,
    }]
    previews = [{
        "op": "set_strip_slot",
        "slot_id": resolved_slot,
        "metric_id": metric_id,
        "title": props.get("title"),
        "valueText": props.get("valueText"),
        "deltaText": props.get("deltaText"),
        "sub": props.get("sub"),
        "reason": props.get("reason"),
        "label": (
            f"{resolved_slot} → {props.get('title')}"
            f"（{props.get('valueText') or '—'}）"
        ),
    }]
    return {
        "ok": True,
        "region": "strip_metric",
        "action": "set_strip_slot",
        "metric_id": metric_id,
        "slot_id": resolved_slot,
        "stripMetric": props,
        "previews": previews,
        "ops": ops,
        "items": [],
        "ambiguities": [],
        "error": None,
        "suggestions": [],
    }


def interpret_index_board(
    text: str,
    *,
    config: dict[str, Any] | None = None,
    market_strip: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """解析指数一览 NL：追加 / 移除。"""
    t = (text or "").strip()
    if not t:
        return {
            "ok": False,
            "region": "index_board",
            "error": "empty_text",
            "error_zh": "请描述要加/去掉的指数，例如：加上中证1000",
            "ops": [],
            "previews": [],
        }

    action = "append"
    rest = t
    if any(p in t for p in _CLEAR_PHRASES) or "恢复默认" in t or "重置" in t:
        return {
            "ok": True,
            "region": "index_board",
            "action": "reset_index_board",
            "ops": [{"op": "reset_index_board"}],
            "previews": [{
                "op": "reset_index_board",
                "label": "恢复默认指数一览名单",
            }],
            "error": None,
        }
    if any(v in t for v in _REMOVE_VERBS):
        action = "remove"
        for v in _REMOVE_VERBS:
            rest = rest.replace(v, " ")
    else:
        for v in _APPEND_VERBS:
            rest = rest.replace(v, " ")
    rest = rest.strip()
    for noise in ("指数", "一下", "到", "板", "一览"):
        rest = rest.replace(noise, " ")
    rest = rest.strip() or t

    # 优先 catalog 指数名 / 直接 code
    code: str | None = None
    name: str | None = None
    cu = rest.upper().replace(" ", "")
    if CODE_RE.match(cu):
        code = cu
        name = cu
    else:
        from kss.ui_surface.bind_catalog import search as catalog_search

        cat = catalog_search("index_board", rest, limit=5)
        # 指数可能在 strip 的 index 类 metric 里；也扫 indexBoard 默认名
        if cat.get("ok") and cat.get("items"):
            it = cat["items"][0]
            codes = it.get("codes") or {}
            code = (
                codes.get("index_code")
                or codes.get("primary")
                or codes.get("code")
            )
            names = it.get("names") or []
            name = names[0] if names else code
        if not code:
            # 默认板名称兜底
            _DEFAULT_NAMES = {
                "上证指数": "000001.SH", "上证": "000001.SH",
                "深证成指": "399001.SZ", "深成指": "399001.SZ",
                "创业板指": "399006.SZ", "创业板": "399006.SZ",
                "科创50": "000688.SH", "科创100": "000698.SH",
                "科创综指": "000680.SH",
                "沪深300": "000300.SH", "上证50": "000016.SH",
                "中证500": "000905.SH", "中证1000": "000852.SH",
                "中证A500": "000510.SH", "中证2000": "932000.CSI",
                "北证50": "899050.BJ",
            }
            for k, v in sorted(_DEFAULT_NAMES.items(), key=lambda x: -len(x[0])):
                if k in rest or k in t:
                    code, name = v, k
                    break
        if not code:
            return {
                "ok": False,
                "region": "index_board",
                "error": "unknown_index",
                "error_zh": f"未识别指数「{rest or t}」。可说：加上中证1000、去掉北证50",
                "suggestions": ["加上中证1000", "去掉北证50", "恢复默认"],
                "ops": [],
                "previews": [],
            }

    code = str(code).upper()
    name = name or code
    op_name = "index_board_append" if action == "append" else "index_board_remove"
    # 预览：从 strip 取价
    close = pct = None
    strip = market_strip or {}
    for board_key in ("indexBoard", "indices"):
        for idx in strip.get(board_key) or []:
            if str(idx.get("code", "")).upper() == code:
                close = idx.get("close")
                pct = idx.get("pct")
                name = idx.get("name") or name
                break
    previews = [{
        "op": op_name,
        "code": code,
        "name": name,
        "close": close,
        "pct": pct,
        "label": (
            f"{'追加' if action == 'append' else '移除'} {name}（{code}）"
            + (f" {close} {pct:+.2f}%" if close is not None and pct is not None else "")
        ),
    }]
    # 未改过用户板时 remove/append 需基于 effective 全量
    cfg = config if config is not None else load_config()
    current = effective_index_board_codes(cfg)
    if action == "append":
        if code in current:
            new_codes = list(current)
        else:
            new_codes = list(current) + [code]
        ops = [{"op": "index_board_set", "codes": new_codes}]
    else:
        new_codes = [c for c in current if c != code]
        if not new_codes:
            return {
                "ok": False,
                "region": "index_board",
                "error": "empty_board",
                "error_zh": "指数一览至少保留 1 只",
                "ops": [],
                "previews": previews,
            }
        ops = [{"op": "index_board_set", "codes": new_codes}]

    return {
        "ok": True,
        "region": "index_board",
        "action": op_name,
        "ops": ops,
        "previews": previews,
        "items": [{"status": "ok", "code": code, "name": name}],
        "error": None,
        "suggestions": [],
    }


def interpret(
    region: str,
    text: str,
    *,
    config: dict[str, Any] | None = None,
    market_strip: dict[str, Any] | None = None,
    probe_fn: ProbeFn | None = None,
    slot_id: str | None = None,
) -> dict[str, Any]:
    """统一入口。"""
    r = (region or "").strip().lower()
    if r in ("overnight_us", "overnight", "overnight_us_marquee"):
        return interpret_overnight(text, config=config, probe_fn=probe_fn)
    if r in (
        "strip_metric", "metric", "strip_metric_slot",
        "strip_slots", "strip",
    ):
        return interpret_strip_metric(
            text, market_strip=market_strip, slot_id=slot_id,
        )
    if r in ("index_board", "indices", "indexboard"):
        return interpret_index_board(
            text, config=config, market_strip=market_strip,
        )
    return {
        "ok": False,
        "error": "bad_region",
        "error_zh": (
            f"未知 region：{region}"
            "（可用 overnight_us / strip_metric / index_board）"
        ),
        "ops": [],
        "previews": [],
    }
