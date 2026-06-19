"""开盘啦/同花顺板块轮动原始接口适配器.

数据源：``duanxianxia.com`` 的四个板块轮动接口，被上游
``hssqz/plate-rotation-skill`` 使用：

- ``/api/getPlateRotatData``：板块轮动主表（THS 涨幅 / KAIPAN 强度分）
- ``/api/getPlateRotatChart``：Top5 板块 N 日排名变化曲线
- ``/api/getLongByPlate``：某板块每日龙头股矩阵（龙一~龙五）
- ``/api/getPlateDayChart``：单板块 N 日强度+量能时序

设计纪律（对齐 :mod:`kss.data.ths_client`）：

- 数据层契约：网络错 / 非 200 / JSON 异常 / 关键字段缺失 → 返回 ``None`` +
  warning，不外抛.
- 解析只依赖 ``re`` 与标准库，不依赖外部 HTML 解析器.
- 板块代码前缀约定：``88x`` 为同花顺概念，``80x``/``803x`` 为开盘啦概念.
- 金额/分数字段保留原始单位，输出字典中显式标注.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from typing import Final

logger = logging.getLogger(__name__)

_BASE_URL: Final[str] = "https://duanxianxia.com"
_HEADERS: Final[dict[str, str]] = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://duanxianxia.com/web/main",
    "Origin": "https://duanxianxia.com",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}
_TIMEOUT_SECONDS: Final[float] = 15.0
_MAX_ATTEMPTS: Final[int] = 2
_BACKOFF_BASE_SECONDS: Final[float] = 1.0


def _post(path: str, params: dict[str, str | int]) -> dict | None:
    """发送 POST 请求并返回 JSON，失败时返回 None."""
    url = f"{_BASE_URL}{path}"
    body = urllib.parse.urlencode(params, doseq=True).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=_HEADERS, method="POST")

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
                txt = resp.read().decode("utf-8", errors="replace")
                return json.loads(txt)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt >= _MAX_ATTEMPTS:
                logger.warning("[plate_rotation] %s 最终失败: %s", path, exc)
                return None
            wait = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            logger.info("[plate_rotation] %s 第 %d 次失败: %s；%.1fs 后重试", path, attempt, exc, wait)
            time.sleep(wait)
    return None


def _parse_plate_rotat(html: str) -> list[dict]:
    """解析板块轮动主表 HTML，返回今日 Top 板块清单."""
    out: list[dict] = []
    rows = re.split(r"<span class='rank'[^>]*>(\d+)</span>", html)
    for i in range(1, len(rows), 2):
        rank = int(rows[i])
        rest = rows[i + 1] if i + 1 < len(rows) else ""
        m = re.search(
            r"<td class='plate plate\d+'\s*code='(\d+)'\s*name='([^']+)'[^>]*>"
            r".*?<span style='color:(red|green);'>([\d.\-]+%?)</span>",
            rest, re.S,
        )
        if not m:
            continue
        code, name, color, value = m.groups()
        out.append({
            "rank": rank,
            "code": code,
            "name": name,
            "value": value,
            "value_type": "pct" if value.endswith("%") else "score",
            "color": color,
        })
    return out


def _parse_plate_rotat_dates(html: str) -> list[str]:
    """从板块轮动主表表头抽取日期序列（newest first）."""
    return re.findall(r"line-height:160%;'>(\d{4}-\d{2}-\d{2})", html)


def _parse_plate_long_heads(html: str, dates: list[str]) -> list[dict]:
    """解析每日龙头股矩阵 HTML."""
    tds = re.findall(
        r"<td style='(?:text-align:left;padding-bottom:5px;"
        r"|text-align:center;color:#bbb[^']*)'>(.*?)(?=<td|$)",
        html, re.S,
    )
    out: list[dict] = []
    for di, td in enumerate(tds):
        if "当日无领涨" in td:
            heads: list[dict] = []
        else:
            heads = [
                {"rank": rank, "code": code, "name": name}
                for code, rank, name in re.findall(
                    r"<div class='kline' code='(\d{6})'><span>([^<]+)</span>([^<]+)</div>",
                    td,
                )
            ]
        out.append({
            "date": dates[di] if di < len(dates) else None,
            "heads": heads,
        })
    return out


def fetch_plate_rotat_data(
    source: str,
    days: int = 20,
) -> dict | None:
    """拉取板块轮动主表.

    Args:
        source: ``ths`` 返回涨幅 %，``kaipan`` 返回强度分.
        days: 回溯天数（10|20|30|50）.

    Returns:
        ``{"date": [...], "today_top": [...]}`` 或失败时 ``None``.
    """
    data = _post("/api/getPlateRotatData", {"from": source, "days": days})
    if data is None:
        return None
    html = data.get("html")
    if not isinstance(html, str):
        logger.warning("[plate_rotation] getPlateRotatData 缺少 html 字段")
        return None
    return {
        "date": _parse_plate_rotat_dates(html),
        "today_top": _parse_plate_rotat(html),
    }


def fetch_plate_rotat_chart(
    source: str,
    days: int = 20,
) -> dict | None:
    """拉取 Top5 板块 N 日排名变化曲线.

    Args:
        source: ``ths`` 或 ``kaipan``.
        days: 回溯天数.

    Returns:
        上游原始 JSON（含 ``date`` / ``legend`` / ``name`` / ``1``..``5``），
        失败时 ``None``.
    """
    data = _post("/api/getPlateRotatChart", {"from": source, "days": days})
    if data is None:
        return None
    if "date" not in data or "name" not in data:
        logger.warning("[plate_rotation] getPlateRotatChart 缺少 date/name 字段")
        return None
    return data


def fetch_long_by_plate(
    platecode: str,
    days: int = 20,
) -> dict | None:
    """拉取某板块每日龙头股矩阵.

    Args:
        platecode: 板块代码，如 ``886084`` / ``801807``.
        days: 回溯天数.

    Returns:
        ``{"platecode": ..., "dates": [...], "daily_heads": [...]}`` 或 ``None``.
    """
    rotat = fetch_plate_rotat_data(
        "ths" if platecode.startswith("88") else "kaipan", days=days
    )
    if rotat is None:
        return None
    dates = rotat.get("date") or []

    data = _post("/api/getLongByPlate", {"platecode": platecode, "days": days})
    if data is None:
        return None
    html = data.get("html")
    if not isinstance(html, str):
        logger.warning("[plate_rotation] getLongByPlate 缺少 html 字段")
        return None
    return {
        "platecode": platecode,
        "dates": dates,
        "daily_heads": _parse_plate_long_heads(html, dates),
    }


def rank_plate_long_persistence(
    long_by_plate: dict,
    top_n: int = 15,
) -> list[dict]:
    """统计板块龙头股跨天频次（妖王榜）.

    Args:
        long_by_plate: :func:`fetch_long_by_plate` 返回值.
        top_n: 返回前几名.

    Returns:
        按上榜次数降序排列的龙头列表.
    """
    bag: dict[str, dict] = defaultdict(lambda: {"count": 0, "name": "", "positions": []})
    for d in long_by_plate.get("daily_heads", []):
        date = d.get("date")
        for h in d.get("heads", []):
            slot = bag[h["code"]]
            slot["count"] += 1
            slot["name"] = h["name"]
            slot["positions"].append(f"{date}/{h['rank']}")
    ranked = sorted(bag.items(), key=lambda x: -x[1]["count"])
    return [{"code": c, **info} for c, info in ranked[:top_n]]


def fetch_plate_day_chart(
    platecode: str,
    days: int = 20,
) -> dict | None:
    """拉取单板块 N 日强度+量能时序.

    Args:
        platecode: 板块代码.
        days: 回溯天数.

    Returns:
        上游原始 JSON（含 ``legend`` / ``date`` / series），失败时 ``None``.
        ``legend`` 为 ``null`` 表示该板块近 N 日未活跃.
    """
    data = _post("/api/getPlateDayChart", {"platecode": platecode, "days": days})
    if data is None:
        return None
    if "date" not in data:
        logger.warning("[plate_rotation] getPlateDayChart 缺少 date 字段")
        return None
    return data
