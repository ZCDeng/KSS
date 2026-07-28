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
from kss.ui_surface.config import CODE_RE, NORTH_METRICS, default_codes, load_config
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
    """token → {status, code?, name?, kind?, error?}"""
    raw = token.strip()
    if not raw:
        return {"status": "failed", "token": token, "error": "empty_token"}

    # 别名
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

    # 直接 ticker
    code = raw.upper()
    if CODE_RE.match(code):
        return {
            "status": "ok",
            "token": token,
            "code": code,
            "name": code,
            "kind": "yfinance",
        }

    return {
        "status": "failed",
        "token": token,
        "error": "unknown_symbol",
        "error_zh": f"无法识别「{raw}」，可说：苹果、英伟达、AAPL",
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


def interpret_strip_metric(
    text: str,
    *,
    market_strip: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """解析指标小卡 NL。"""
    t = (text or "").strip()
    if not t:
        return {
            "ok": False,
            "region": "strip_metric",
            "action": None,
            "error": "empty_text",
            "error_zh": "请输入指标名，例如：改成封板率",
            "suggestions": list(METRIC_SUGGESTIONS),
            "previews": [],
            "ops": [],
        }

    if is_north_utterance(t):
        return {
            "ok": False,
            "region": "strip_metric",
            "action": "set_strip_metric",
            "error": "north_forbidden",
            "error_zh": "第一行已固定展示北向资金，小卡不能再绑北向类指标",
            "suggestions": list(METRIC_SUGGESTIONS),
            "previews": [],
            "ops": [],
        }

    # 去掉动词前缀
    _, rest = _strip_leading_verbs(t, _METRIC_VERBS)
    # 再去掉「指标」等
    rest = rest.strip()
    for noise in ("指标", "小卡", "显示", "一下"):
        rest = rest.replace(noise, " ")
    rest = rest.strip() or t

    metric_id = lookup_metric(rest)
    if not metric_id:
        # 尝试整句别名
        metric_id = lookup_metric(t)
    if not metric_id:
        # 子串匹配登记别名键
        from kss.ui_surface.aliases import METRIC_ALIASES

        for key, mid in sorted(METRIC_ALIASES.items(), key=lambda x: -len(x[0])):
            if key in t or key in rest:
                metric_id = mid
                break

    if not metric_id or metric_id not in METRIC_CATALOG:
        return {
            "ok": False,
            "region": "strip_metric",
            "action": "set_strip_metric",
            "error": "unknown_metric",
            "error_zh": f"暂不支持「{rest or t}」。可说：" + "、".join(METRIC_SUGGESTIONS),
            "suggestions": list(METRIC_SUGGESTIONS),
            "previews": [],
            "ops": [],
        }

    if metric_id in NORTH_METRICS:
        return {
            "ok": False,
            "region": "strip_metric",
            "action": "set_strip_metric",
            "error": "north_forbidden",
            "error_zh": "第一行已固定展示北向资金",
            "suggestions": list(METRIC_SUGGESTIONS),
            "previews": [],
            "ops": [],
        }

    props = resolve_metric_props(market_strip, metric_id)
    ops = [{"op": "set_strip_metric", "metric_id": metric_id}]
    previews = [{
        "op": "set_strip_metric",
        "metric_id": metric_id,
        "title": props.get("title"),
        "valueText": props.get("valueText"),
        "deltaText": props.get("deltaText"),
        "sub": props.get("sub"),
        "reason": props.get("reason"),
        "label": f"切换为 {props.get('title')}（{props.get('valueText') or '—'}）",
    }]
    return {
        "ok": True,
        "region": "strip_metric",
        "action": "set_strip_metric",
        "metric_id": metric_id,
        "stripMetric": props,
        "previews": previews,
        "ops": ops,
        "items": [],
        "ambiguities": [],
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
) -> dict[str, Any]:
    """统一入口。"""
    r = (region or "").strip().lower()
    if r in ("overnight_us", "overnight", "overnight_us_marquee"):
        return interpret_overnight(text, config=config, probe_fn=probe_fn)
    if r in ("strip_metric", "metric", "strip_metric_slot"):
        return interpret_strip_metric(text, market_strip=market_strip)
    return {
        "ok": False,
        "error": "bad_region",
        "error_zh": f"未知 region：{region}（可用 overnight_us / strip_metric）",
        "ops": [],
        "previews": [],
    }
