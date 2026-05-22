"""Tushare tools for datasette-agent.

Exposes three async tools the agent can call when the local kss.db is missing data:
    - tushare_stock_basic(ts_code)
    - tushare_daily(ts_code, start_date, end_date)
    - tushare_moneyflow_hsgt(start_date, end_date)

Token comes from env TUSHARE_TOKEN. Calls run in a thread pool so they don't
block datasette's event loop.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
from functools import lru_cache
from typing import Any

try:
    from datasette import hookimpl
except ImportError:
    def hookimpl(fn):  # tests can import without datasette installed
        return fn

try:
    from datasette_agent.tools import AgentTool
except ImportError:
    AgentTool = None  # plugin still loads if datasette-agent absent

from . import _cache, _scan, _viz

# Which datasette database to write cached rows into. Override via plugin
# config in datasette metadata.yml if you mount kss.db under a different name.
CACHE_DB_NAME = "kss"

# Permission gating: actors without this permission see no tushare tools at all.
# Grant via metadata.yml: permissions: { kss-tushare-fetch: { id: [zhic] } }
FETCH_PERMISSION = "kss-tushare-fetch"

DISPLAY_SCHEMA = {
    "type": "string",
    "enum": ["model", "both", "user"],
    "default": "both",
    "description": (
        "Where results go. 'model'=只给你 reasoning（省 token，rows 不展示给用户）; "
        "'both'=你和用户都看到（你可以评论数据）; 'user'=渲染给用户，你只看到 row_count（show-me 场景）。"
    ),
}


@lru_cache(maxsize=1)
def _pro_api():
    import tushare as ts
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN not set in environment")
    ts.set_token(token)
    return ts.pro_api()


async def _run(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


async def _write_back(datasette, upsert_fn, df) -> int | None:
    """Persist df via the named datasette database. Returns rows written, or None
    if datasette is unavailable (e.g. CLI dry-run / unit test)."""
    if datasette is None or df is None or len(df) == 0:
        return None
    try:
        db = datasette.get_database(CACHE_DB_NAME)
    except KeyError:
        return None

    def _do(conn):
        n = upsert_fn(conn, df)
        conn.commit()
        return n

    try:
        return await db.execute_write_fn(_do)
    except Exception:
        # Don't break the tool if cache write fails — agent still gets the data.
        return None


def _df_to_html(df, caption: str) -> str:
    if df is None or len(df) == 0:
        return f'<p><em>{html.escape(caption)}: 无数据</em></p>'
    head = df.head(50).to_html(index=False, border=0, classes="kss-tushare-table")
    more = f'<p><small>共 {len(df)} 行，显示前 50</small></p>' if len(df) > 50 else ""
    return f'<h4>{html.escape(caption)}</h4>{head}{more}'


def _apply_display(payload: dict, df, caption: str, display: str) -> dict:
    """Reshape payload per display mode, mirroring datasette-agent's sql_query convention.

    - model: rows visible to LLM, no _html (user sees nothing inline)
    - both:  rows visible to LLM + _html rendered to user
    - user:  _html rendered, raw rows hidden from LLM (only row_count surfaces)
    """
    if display not in ("model", "both", "user"):
        display = "both"
    rows = df.to_dict(orient="records") if df is not None and len(df) else []
    if display == "model":
        payload["rows"] = rows
    elif display == "both":
        payload["rows"] = rows
        payload["_html"] = _df_to_html(df, caption)
    else:  # user
        payload["_rows"] = rows
        payload["_html"] = _df_to_html(df, caption)
        payload["row_count"] = len(rows)
    return payload


async def _stock_basic(datasette, actor, ts_code: str, display: str = "both") -> str:
    def call():
        return _pro_api().stock_basic(ts_code=ts_code, fields=(
            "ts_code,name,area,industry,market,list_date,is_hs"
        ))
    df = await _run(call)
    cached = await _write_back(datasette, _cache.upsert_stock_basic, df)
    payload = {"cached_rows": cached}
    _apply_display(payload, df, f"基本资料 {ts_code}", display)
    return json.dumps(payload, ensure_ascii=False)


async def _daily(datasette, actor, ts_code: str, start_date: str, end_date: str,
                 display: str = "both") -> str:
    def call():
        return _pro_api().daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    df = await _run(call)
    cached = await _write_back(datasette, _cache.upsert_daily, df)
    payload: dict[str, Any] = {
        "ts_code": ts_code, "start": start_date, "end": end_date,
        "cached_rows": cached,
    }
    if df is not None and len(df):
        payload["last_close"] = float(df.iloc[0]["close"])
        payload["period_pct_chg"] = round(
            (df.iloc[0]["close"] / df.iloc[-1]["pre_close"] - 1) * 100, 2
        )
    _apply_display(payload, df, f"{ts_code} 日线 {start_date}–{end_date}", display)
    return json.dumps(payload, ensure_ascii=False)


async def _moneyflow_hsgt(datasette, actor, start_date: str, end_date: str,
                          display: str = "both") -> str:
    def call():
        return _pro_api().moneyflow_hsgt(start_date=start_date, end_date=end_date)
    df = await _run(call)
    cached = await _write_back(datasette, _cache.upsert_hsgt, df)
    payload: dict[str, Any] = {
        "start": start_date, "end": end_date, "cached_rows": cached,
    }
    if df is not None and len(df) and "north_money" in df:
        payload["net_amount_sum"] = float(df["north_money"].sum())
    _apply_display(payload, df, f"北向资金 {start_date}–{end_date}", display)
    return json.dumps(payload, ensure_ascii=False)


try:
    from datasette.permissions import Action
    from datasette.resources import DatabaseResource
except ImportError:
    Action = None
    DatabaseResource = None


async def _run_sql_to_df(datasette, actor, database: str, sql: str):
    """Execute read-only SQL through datasette's permission-checked path.
    Returns (df, error_html). On any failure df is None and error_html is set."""
    import pandas as pd
    if datasette is None:
        return None, "<p><em>datasette context missing</em></p>"
    if DatabaseResource is not None:
        ok = await datasette.allowed(
            action="execute-sql",
            resource=DatabaseResource(database=database),
            actor=actor,
        )
        if not ok:
            return None, f"<p><em>permission denied on {html.escape(database)}</em></p>"
    if database not in datasette.databases:
        return None, f"<p><em>db '{html.escape(database)}' not found</em></p>"
    db = datasette.get_database(database)
    try:
        result = await db.execute(sql, truncate=True)
    except Exception as e:
        return None, f"<p><em>SQL error: {html.escape(str(e))}</em></p>"
    df = pd.DataFrame([dict(r) for r in result.rows], columns=list(result.columns))
    return df, None


async def _viz_candlestick(datasette, actor, database: str, sql: str,
                           title: str = "") -> str:
    df, err = await _run_sql_to_df(datasette, actor, database, sql)
    if err:
        return json.dumps({"_html": err, "error": "see _html"}, ensure_ascii=False)
    html_img = await asyncio.to_thread(_viz.candlestick_html, df, title)
    summary: dict[str, Any] = {"rows_charted": len(df), "title": title or None}
    if len(df):
        summary["close_min"] = float(df["close"].min())
        summary["close_max"] = float(df["close"].max())
        summary["close_last"] = float(df.sort_values("trade_date").iloc[-1]["close"])
    summary["_html"] = html_img
    summary["_edit_sql_url"] = f"/{database}/-/query?sql={html.escape(sql)}"
    return json.dumps(summary, ensure_ascii=False)


async def _viz_moneyflow_stack(datasette, actor, database: str, sql: str,
                               title: str = "") -> str:
    df, err = await _run_sql_to_df(datasette, actor, database, sql)
    if err:
        return json.dumps({"_html": err, "error": "see _html"}, ensure_ascii=False)
    html_img = await asyncio.to_thread(_viz.moneyflow_stack_html, df, title)
    summary: dict[str, Any] = {"rows_charted": len(df), "title": title or None}
    if len(df):
        # Aggregate net flow across all tiers if columns present
        net_total = 0.0
        for prefix in ("sm", "md", "lg", "elg"):
            b, s = f"buy_{prefix}_amount", f"sell_{prefix}_amount"
            if b in df.columns and s in df.columns:
                net_total += float((df[b] - df[s]).sum())
        summary["net_flow_total"] = net_total
    summary["_html"] = html_img
    return json.dumps(summary, ensure_ascii=False)


@hookimpl
def register_actions():
    if Action is None:
        return []
    return [Action(
        name=FETCH_PERMISSION,
        description="Allow KSS tushare-fetch + cache write tools (live API + DB write).",
    )]


@hookimpl
def register_agent_tools(datasette):
    if AgentTool is None:
        return []
    return [
        AgentTool(
            name="tushare_stock_basic",
            description="查 A 股个股基本资料（名称/行业/上市日/沪深港通标识）。库里没基本面信息时用。",
            input_schema={
                "type": "object",
                "properties": {
                    "ts_code": {"type": "string", "description": "如 300750.SZ"},
                    "display": DISPLAY_SCHEMA,
                },
                "required": ["ts_code"],
            },
            fn=_stock_basic,
            required_permission=FETCH_PERMISSION,
        ),
        AgentTool(
            name="tushare_daily",
            description=(
                "拉 A 股日线行情。库里 daily 表没覆盖某个 ts_code 或日期段时用。"
                "返回会写回本地 daily 表做缓存，下次 SQL 查就命中。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ts_code": {"type": "string"},
                    "start_date": {"type": "string", "description": "YYYYMMDD"},
                    "end_date": {"type": "string", "description": "YYYYMMDD"},
                    "display": DISPLAY_SCHEMA,
                },
                "required": ["ts_code", "start_date", "end_date"],
            },
            fn=_daily,
            required_permission=FETCH_PERMISSION,
        ),
        AgentTool(
            name="viz_candlestick",
            description=(
                "把 SQL 查询结果渲染成 K 线图（中国 convention：涨红跌绿），可选叠加成交量子图。"
                "SQL 必须返回列 (trade_date, open, high, low, close)，含 vol 列则自动加成交量子图。"
                "用于'画一下 688322 最近 60 日 K 线'类请求 — 比纯表格直观得多。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "通常是 'kss'"},
                    "sql": {"type": "string", "description": "需返回 trade_date/open/high/low/close[/vol]"},
                    "title": {"type": "string", "description": "图标题，如 '688322.SH 60日'"},
                },
                "required": ["database", "sql"],
            },
            fn=_viz_candlestick,
        ),
        AgentTool(
            name="viz_moneyflow_stack",
            description=(
                "把资金流四档（小/中/大/超大单）渲染成净额堆叠柱状图。"
                "SQL 必须返回 trade_date 列 + 任意几对 (buy_sm_amount, sell_sm_amount), "
                "(buy_md_amount, sell_md_amount), (buy_lg_amount, sell_lg_amount), "
                "(buy_elg_amount, sell_elg_amount) — 用于看资金结构而不是单一净额。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "database": {"type": "string", "description": "通常是 'kss'"},
                    "sql": {"type": "string", "description": "需含 trade_date + 至少一对 buy_X/sell_X"},
                    "title": {"type": "string"},
                },
                "required": ["database", "sql"],
            },
            fn=_viz_moneyflow_stack,
        ),
        AgentTool(
            name="spawn_kss_anomaly_scan",
            description=(
                "启动 KSS 科创板异动扫描的 background agent。给它一句筛选条件描述（如"
                "'净流入>2亿且涨幅>5%'），自己跑 5 步流程：取锚日 → 筛候选 → "
                "逐个查 30 日量价资金 → 汇总 → 写报告。返回 agent_id，过几分钟用 "
                "check_background_agent 看进度。适合夜跑或不希望阻塞当前对话的探索任务。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "criteria": {
                        "type": "string",
                        "description": "自然语言描述的筛选条件，如 'net_mf_amount > 20000 万元 且 pct_chg > 5%'",
                    },
                    "anchor_date": {
                        "type": "string",
                        "description": "锚日 YYYYMMDD；不传则自动取最新交易日",
                    },
                    "lookback_days": {
                        "type": "integer",
                        "default": 5,
                        "description": "在锚日附近向前看几日内的异动",
                    },
                    "top_n": {
                        "type": "integer",
                        "default": 10,
                        "description": "最多展开多少个候选标的写报告",
                    },
                },
                "required": ["criteria"],
            },
            fn=_scan.spawn_kss_anomaly_scan,
            # Requires both datasette-agent-background (to spawn) + kss-tushare-fetch
            # (the spawned agent inherits actor's perms; we gate the *spawning* here).
            required_permission="datasette-agent-background",
        ),
        AgentTool(
            name="tushare_moneyflow_hsgt",
            description="查北向资金（沪深港通）净流入。研究外资动向时用，结果缓存到 moneyflow_hsgt 表。",
            input_schema={
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "YYYYMMDD"},
                    "end_date": {"type": "string", "description": "YYYYMMDD"},
                    "display": DISPLAY_SCHEMA,
                },
                "required": ["start_date", "end_date"],
            },
            fn=_moneyflow_hsgt,
            required_permission=FETCH_PERMISSION,
        ),
    ]
