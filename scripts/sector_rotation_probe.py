#!/usr/bin/env python3
"""板块热点轮动上游接口可用性探针.

临时脚本，用于 Phase 0 验证 duanxianxia.com 四个接口的字段与可用性：
- /api/getPlateRotatData
- /api/getPlateRotatChart
- /api/getLongByPlate
- /api/getPlateDayChart

输出保存到 storage/sector_rotation/probe/，作为后续 adapter 设计的字段对照依据.
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
from pathlib import Path
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
    req = urllib.request.Request(
        url,
        data=body,
        headers=_HEADERS,
        method="POST",
    )

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as r:
                txt = r.read().decode("utf-8", errors="replace")
                return json.loads(txt)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt >= _MAX_ATTEMPTS:
                logger.warning("[probe] %s 最终失败: %s", path, exc)
                return None
            wait = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            logger.info("[probe] %s 第 %d 次失败: %s；%.1fs 后重试", path, attempt, exc, wait)
            time.sleep(wait)
    return None


def _parse_plate_rotat(data: dict, source: str) -> list[dict]:
    """解析 /api/getPlateRotatData，返回当日 Top 板块清单."""
    html = data.get("html", "")
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


def _parse_plate_rotat_dates(data: dict) -> list[str]:
    """从 getPlateRotatData 表头抽取日期序列."""
    html = data.get("html", "")
    return re.findall(r"line-height:160%;'>(\d{4}-\d{2}-\d{2})", html)


def _parse_plate_long_heads(data: dict, dates: list[str]) -> list[dict]:
    """解析 /api/getLongByPlate，返回每日龙头股清单."""
    html = data.get("html", "")
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


def _rank_long_persistence(data: dict, dates: list[str], top_n: int = 15) -> list[dict]:
    """统计跨天龙头频次，返回妖王榜."""
    days = _parse_plate_long_heads(data, dates)
    bag: dict[str, dict] = defaultdict(lambda: {"count": 0, "name": "", "positions": []})
    for d in days:
        for h in d["heads"]:
            slot = bag[h["code"]]
            slot["count"] += 1
            slot["name"] = h["name"]
            slot["positions"].append(f"{d['date']}/{h['rank']}")
    ranked = sorted(bag.items(), key=lambda x: -x[1]["count"])
    return [{"code": c, **info} for c, info in ranked[:top_n]]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out_dir = Path("storage/sector_rotation/probe")
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_cases: list[tuple[str, str, dict[str, str | int]]] = [
        ("plate_rotat_ths", "/api/getPlateRotatData", {"from": "ths", "days": 20}),
        ("plate_rotat_kaipan", "/api/getPlateRotatData", {"from": "kaipan", "days": 20}),
        ("plate_rotat_chart_kaipan", "/api/getPlateRotatChart", {"from": "kaipan", "days": 20}),
        ("long_by_plate", "/api/getLongByPlate", {"platecode": "886084", "days": 20}),
        ("plate_day_chart", "/api/getPlateDayChart", {"platecode": "886084", "days": 20}),
    ]

    raw_cache: dict[str, dict] = {}
    for name, path, params in raw_cases:
        logger.info("[probe] 请求 %s %s", path, params)
        data = _post(path, params)
        if data is None:
            logger.warning("[probe] %s 无响应", name)
            continue
        raw_cache[name] = data
        out_file = out_dir / f"{name}.json"
        out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[probe] %s 原始响应已保存: %s", name, out_file)

    # 解析样例
    parsed: dict[str, object] = {}

    if "plate_rotat_kaipan" in raw_cache:
        parsed["kaipan_top10"] = _parse_plate_rotat(raw_cache["plate_rotat_kaipan"], source="kaipan")[:10]
        parsed["kaipan_dates"] = _parse_plate_rotat_dates(raw_cache["plate_rotat_kaipan"])
        logger.info("[probe] KAIPAN 日期列: %s", parsed["kaipan_dates"][:5])

    if "plate_rotat_ths" in raw_cache:
        parsed["ths_top10"] = _parse_plate_rotat(raw_cache["plate_rotat_ths"], source="ths")[:10]

    if "long_by_plate" in raw_cache and "kaipan_dates" in parsed:
        dates = parsed["kaipan_dates"]
        parsed["long_daily_heads_886084"] = _parse_plate_long_heads(raw_cache["long_by_plate"], dates)[:5]
        parsed["long_persistence_886084"] = _rank_long_persistence(raw_cache["long_by_plate"], dates, top_n=10)
        logger.info("[probe] 886084 妖王榜: %s", parsed["long_persistence_886084"][:3])

    parsed_file = out_dir / "parsed_samples.json"
    parsed_file.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[probe] 解析样例已保存: %s", parsed_file)


if __name__ == "__main__":
    main()
