"""档 A NL 别名表：中文/英文/俗称 → overnight code 或 metric_id。

常量发版；单测钉死黄金句。不热更。
"""

from __future__ import annotations

from typing import Any

# 别名（小写匹配）→ {code, name, kind}
# 含候选表常见中文名 + 默认 universe 名，便于「去掉纳斯达克」识别默认项
SYMBOL_ALIASES: dict[str, dict[str, str]] = {
    # 美股 / ETF
    "苹果": {"code": "AAPL", "name": "苹果", "kind": "yfinance"},
    "apple": {"code": "AAPL", "name": "苹果", "kind": "yfinance"},
    "aapl": {"code": "AAPL", "name": "苹果", "kind": "yfinance"},
    "微软": {"code": "MSFT", "name": "微软", "kind": "yfinance"},
    "microsoft": {"code": "MSFT", "name": "微软", "kind": "yfinance"},
    "msft": {"code": "MSFT", "name": "微软", "kind": "yfinance"},
    "英伟达": {"code": "NVDA", "name": "英伟达", "kind": "yfinance"},
    "nvidia": {"code": "NVDA", "name": "英伟达", "kind": "yfinance"},
    "nvda": {"code": "NVDA", "name": "英伟达", "kind": "yfinance"},
    "阿斯麦": {"code": "ASML", "name": "阿斯麦", "kind": "yfinance"},
    "asml": {"code": "ASML", "name": "阿斯麦", "kind": "yfinance"},
    "超威": {"code": "AMD", "name": "超威半导体", "kind": "yfinance"},
    "超威半导体": {"code": "AMD", "name": "超威半导体", "kind": "yfinance"},
    "amd": {"code": "AMD", "name": "超威半导体", "kind": "yfinance"},
    "亚马逊": {"code": "AMZN", "name": "亚马逊", "kind": "yfinance"},
    "amzn": {"code": "AMZN", "name": "亚马逊", "kind": "yfinance"},
    "谷歌": {"code": "GOOGL", "name": "谷歌A", "kind": "yfinance"},
    "google": {"code": "GOOGL", "name": "谷歌A", "kind": "yfinance"},
    "googl": {"code": "GOOGL", "name": "谷歌A", "kind": "yfinance"},
    "meta": {"code": "META", "name": "Meta", "kind": "yfinance"},
    "脸书": {"code": "META", "name": "Meta", "kind": "yfinance"},
    "台积电": {"code": "TSM", "name": "台积电", "kind": "yfinance"},
    "tsm": {"code": "TSM", "name": "台积电", "kind": "yfinance"},
    "高通": {"code": "QCOM", "name": "高通", "kind": "yfinance"},
    "qcom": {"code": "QCOM", "name": "高通", "kind": "yfinance"},
    "英特尔": {"code": "INTC", "name": "英特尔", "kind": "yfinance"},
    "intc": {"code": "INTC", "name": "英特尔", "kind": "yfinance"},
    "特斯拉": {"code": "TSLA", "name": "特斯拉", "kind": "yfinance"},
    "tsla": {"code": "TSLA", "name": "特斯拉", "kind": "yfinance"},
    "美光": {"code": "MU", "name": "美光科技", "kind": "yfinance"},
    "mu": {"code": "MU", "name": "美光科技", "kind": "yfinance"},
    "博通": {"code": "AVGO", "name": "博通", "kind": "yfinance"},
    "avgo": {"code": "AVGO", "name": "博通", "kind": "yfinance"},
    "qqq": {"code": "QQQ", "name": "纳指100 ETF", "kind": "yfinance"},
    "纳指100": {"code": "QQQ", "name": "纳指100 ETF", "kind": "yfinance"},
    "spy": {"code": "SPY", "name": "标普500 ETF", "kind": "yfinance"},
    "标普": {"code": "SPY", "name": "标普500 ETF", "kind": "yfinance"},
    "标普500": {"code": "SPY", "name": "标普500 ETF", "kind": "yfinance"},
    "iwm": {"code": "IWM", "name": "罗素2000 ETF", "kind": "yfinance"},
    "kweb": {"code": "KWEB", "name": "中概互联ETF", "kind": "yfinance"},
    "中概": {"code": "KWEB", "name": "中概互联ETF", "kind": "yfinance"},
    "中概互联": {"code": "KWEB", "name": "中概互联ETF", "kind": "yfinance"},
    "mchi": {"code": "MCHI", "name": "MSCI中国指数ETF", "kind": "yfinance"},
    "msci中国": {"code": "MCHI", "name": "MSCI中国指数ETF", "kind": "yfinance"},
    "robo": {"code": "ROBO", "name": "ROBO全球机器人", "kind": "yfinance"},
    "机器人": {"code": "ROBO", "name": "ROBO全球机器人", "kind": "yfinance"},
    "botz": {"code": "BOTZ", "name": "GX机器人与AI", "kind": "yfinance"},
    "soxx": {"code": "SOXX", "name": "半导体ETF-iShares", "kind": "yfinance"},
    "半导体etf": {"code": "SOXX", "name": "半导体ETF-iShares", "kind": "yfinance"},
    "smh": {"code": "SMH", "name": "半导体ETF-VanEck", "kind": "yfinance"},
    # 指数（默认名单内）
    "纳指": {"code": "IXIC", "name": "纳斯达克综合指数", "kind": "index_global"},
    "纳斯达克": {"code": "IXIC", "name": "纳斯达克综合指数", "kind": "index_global"},
    "纳斯达克综合指数": {"code": "IXIC", "name": "纳斯达克综合指数", "kind": "index_global"},
    "ixic": {"code": "IXIC", "name": "纳斯达克综合指数", "kind": "index_global"},
    "道指": {"code": "DJI", "name": "道琼斯指数", "kind": "index_global"},
    "道琼斯": {"code": "DJI", "name": "道琼斯指数", "kind": "index_global"},
    "道琼斯指数": {"code": "DJI", "name": "道琼斯指数", "kind": "index_global"},
    "dji": {"code": "DJI", "name": "道琼斯指数", "kind": "index_global"},
    "a50": {"code": "XIN9", "name": "富时中国A50指数", "kind": "index_global"},
    "富时a50": {"code": "XIN9", "name": "富时中国A50指数", "kind": "index_global"},
    "xin9": {"code": "XIN9", "name": "富时中国A50指数", "kind": "index_global"},
}

# 别名 → metric_id
METRIC_ALIASES: dict[str, str] = {
    "最高连板": "limit_max_board",
    "连板高度": "limit_max_board",
    "连板": "limit_max_board",
    "maxboard": "limit_max_board",
    "limit_max_board": "limit_max_board",
    "封板率": "limit_seal_rate",
    "封板": "limit_seal_rate",
    "sealrate": "limit_seal_rate",
    "limit_seal_rate": "limit_seal_rate",
    "科创50": "index_kcb50",
    "科创": "index_kcb50",
    "kcb50": "index_kcb50",
    "index_kcb50": "index_kcb50",
    "创业板指": "index_cyb",
    "创业板": "index_cyb",
    "cyb": "index_cyb",
    "index_cyb": "index_cyb",
}

METRIC_SUGGESTIONS = (
    "最高连板",
    "封板率",
    "科创50",
    "创业板指",
)

# 与 config.NORTH_METRICS 对齐的口语
NORTH_UTTERANCE_MARKERS = (
    "北向",
    "北向资金",
    "沪深港通",
    "north_money",
    "north",
)


def lookup_symbol(token: str) -> dict[str, str] | None:
    """返回 {code,name,kind} 或 None。"""
    t = (token or "").strip().lower()
    if not t:
        return None
    hit = SYMBOL_ALIASES.get(t)
    if hit:
        return dict(hit)
    # 大写 ticker 直通由表外 CODE 处理
    return None


def lookup_metric(token: str) -> str | None:
    t = (token or "").strip().lower()
    if not t:
        return None
    # 表内键已是小写友好；中文保持原样再试
    if t in METRIC_ALIASES:
        return METRIC_ALIASES[t]
    raw = (token or "").strip()
    return METRIC_ALIASES.get(raw) or METRIC_ALIASES.get(raw.lower())


def is_north_utterance(text: str) -> bool:
    t = text or ""
    return any(m in t for m in NORTH_UTTERANCE_MARKERS)


def build_symbol_alias_index_from_candidates(candidates: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """把候选表 name 也并进查找（小写 name → row）。"""
    out = dict(SYMBOL_ALIASES)
    for row in candidates:
        code = str(row.get("code") or "").upper()
        name = str(row.get("name") or "").strip()
        kind = str(row.get("kind") or "yfinance")
        if not code:
            continue
        payload = {"code": code, "name": name or code, "kind": kind}
        out.setdefault(code.lower(), payload)
        if name:
            out.setdefault(name.lower(), payload)
            out.setdefault(name, payload)
    return out
