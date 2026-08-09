#!/usr/bin/env python3
"""U6a：kss-mcp —— 把 bridge 命令集包成本地 stdio MCP server。

让 Claude Code / 任意 MCP client 直接查 KSS 实时数据、跑同款逻辑（与 SwiftUI app 同一
dispatch 面，零逻辑 fork）。读命令直接成 tool；写/run/cron-mutation **paper-only by default**：
仅当 `KSS_MCP_LIVE=1`（启动读一次，防 agent 中途翻转，KTD5）才注册写 tool，且每调用须 confirm=True。

威胁模型（诚实记录）：本地 stdio，同用户进程可达。被注入的 agent 在 live 模式下经 confirm 仍能触发
写命令 —— confirm 来自 agent 自身不来自人。自用单机可接受；要更强须 app 侧一次性 token（deferred）。

运行：MCP client 以 `python scripts/kss_mcp.py` 启动（需 fastmcp，进 U0 lock / U2 bootstrap venv）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in (str(_REPO), str(_REPO / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import kss_app_bridge as bridge  # noqa: E402
from fastmcp import FastMCP  # noqa: E402

# KTD5：启动时读一次，运行中不重读 —— agent 无法中途把只读会话翻成可写。
_LIVE = os.environ.get("KSS_MCP_LIVE") == "1"

mcp = FastMCP("kss")


def _call(command: str, args: list[str] | None = None):
    """统一经 bridge.dispatch；与 app 同一逻辑面、同样的代码渲染数字（不经 LLM 复述）。"""
    return bridge.dispatch(command, args or [])


# ---- 读命令（ungated）----

@mcp.tool
def get_snapshot() -> dict:
    """今日总览快照：指数、推荐股、复盘/回测计数等。"""
    return _call("snapshot")


@mcp.tool
def get_stock(symbol: str) -> dict:
    """单只股票明细（日线派生指标）。symbol 形如 688114.SH。"""
    return _call("stock", [symbol])


@mcp.tool
def get_sector_rotation(date: str = "") -> dict:
    """板块热点轮动快照；date 为 YYYYMMDD，空则最新。"""
    return _call("sector-rotation", [date] if date else [])


@mcp.tool
def get_sector_rotation_history(limit: int = 30) -> dict:
    """板块轮动历史归档（近 limit 条）。"""
    return _call("sector-rotation-history", [str(limit)])


@mcp.tool
def get_signal_cards(
    symbol: str = "",
    date: str = "",
    days: str = "",
    card_type: str = "",
) -> dict:
    """确定性信号卡中间层（ETF 申赎/板块异动/主题龙头/放量/估值/回测裁决）。

    空 date 返回最新有卡交易日；无卡返回空列表。优先用本工具做交叉分析，
    需要 ETF 原始份额流时再调 get_etf_radar。
    """
    return _call("signal-cards", [symbol, date, days, card_type])


@mcp.tool
def get_etf_radar(date: str = "") -> dict:
    """ETF 申赎雷达原始快照；date 为 YYYYMMDD，空则最新。"""
    return _call("etf-radar", [date] if date else [])


@mcp.tool
def get_daily_review_archive(symbol: str = "", limit: int = 20) -> dict:
    """个股复盘归档索引；symbol 可选，limit 默认 20。"""
    return _call("daily-review-archive", [symbol, str(limit)])


@mcp.tool
def get_theme_leaders() -> dict:
    """主题龙头梯队。"""
    return _call("theme-leaders")


@mcp.tool
def get_discovery_candidates() -> dict:
    """潜力股发现候选合并。"""
    return _call("get-discovery-candidates")


@mcp.tool
def get_perilla_enrichment(symbol: str) -> dict:
    """紫苏叶个股富化：机构持仓动态 + PE 历史分位 + 美股对标估值。

    symbol 形如 688012.SH，须在紫苏叶列表(core/main)内；否则返回
    not_in_perilla_list。各数据源独立降级（缺失标 unavailable/no_peer）。
    """
    return _call("perilla-enrichment", [symbol])


@mcp.tool
def get_investability_map(symbols: str = "") -> dict:
    """可投资地图节点树：103 个子行业节点的五色暴露分类、所属主轴与组、读法与源文依据。

    五色是**人工先验判断**不是核实事实：深绿=底座/低暴露、浅绿=替代/政策主航道、
    黄=外需/全球定价、橙=许可/点名博弈、紫=反制筹码。转述时须注明这是判断层，
    不得据此给买卖或合规结论。源文未给到节点级色标处主色为 pending。

    symbols 可选，逗号分隔；给了就把这些票按主副节点挂到各节点上，并给出节点三态
    （has_stocks / confirmed_empty 已人工确认无标的 / unreviewed 未核）。
    未核不等于无暴露，不要把 unreviewed 讲成「该方向没有暴露」。
    """
    return _call("investability-map", [symbols])


@mcp.tool
def get_investability_exposure(symbols: str) -> dict:
    """个股暴露信息：行业色、主副节点路径、8 问暴露区位、标注更新时间。

    symbols 必填，逗号分隔。区位串由 Python 侧算完直接返回（红区 · 已定 6/8 这种），
    不要自己重算或改写。个股三态：unlabelled 未上图、pending_color 已上图但该节点
    主色待定、labelled 已标注。行业色与暴露区位是两个并列维度，区位不改写行业色。

    标注是用户手工录入的判断，覆盖面有限；查不到不代表该股无风险。
    """
    return _call("investability-stocks", [symbols])


@mcp.tool
def get_investability_quota(symbols: str, cap_pct: str = "") -> dict:
    """组合暴露配额：五色主轨占比 + 红区副轨占比，两轨不相加。

    symbols 必填且须由调用方显式给出——自选列表的真源在桌面端，库里那张表是同步
    失败会被静默吞掉的镜像，从镜像算配额会漂移。返回值的 denominatorSource 标明
    分母口径。分母只含已标注且主色非待定的票；已标注不足 10 只时不出百分比，
    只给只数并置 sampleInsufficient。

    cap_pct 可选，橙加紫合计上限；给了才返回 overCap 判定。
    """
    return _call("investability-summary", [symbols, cap_pct])


@mcp.tool
def get_paper_summary() -> dict:
    """模拟盘推荐跟踪汇总。"""
    return _call("paper-summary")


@mcp.tool
def get_report(path: str) -> dict:
    """读 storage 下某 markdown 报告（路径相对 state root，受穿越护栏约束）。"""
    return _call("report", [path])


@mcp.tool
def get_trends_month(month: str) -> dict:
    """趋势页某月日历。month 为 YYYY-MM。"""
    return _call("trends-month", [month])


@mcp.tool
def get_trends_day(date: str) -> dict:
    """趋势页某日明细。date 为 YYYY-MM-DD。"""
    return _call("trends-day", [date])


@mcp.tool
def list_cron() -> dict:
    """列出计划任务及其状态。"""
    return _call("cron-list")


@mcp.tool
def get_data_catalog() -> dict:
    """全量数据资产字典：每个数据集的 列/含义/粒度/最近日期/路径（自动反射 schema + 手维含义 overlay）。

五维分析框架索引（U7）：估值→get_stock/get_perilla_enrichment、资金面→get_longbridge_quote/get_sector_rotation、财报质量→get_stock/get_perilla_enrichment、行业景气→get_sector_rotation/get_theme_leaders、事件催化与风险→get_report/research_search。
    """
    return _call("data-catalog")


@mcp.tool
def run_sql_query(sql: str) -> dict:
    """只读分析 SQL(DuckDB 引擎；仅 SELECT/WITH/SUMMARIZE/DESCRIBE，行上限200，5s超时)。

    可查表 = 已割接域的 kss.db 表 + 行情 parquet 数据集；先用 get_data_catalog 了解列含义，
    或用 SHOW TABLES / DESCRIBE <table> 自查。数字结果以本工具返回为准，勿凭记忆复述。
    """
    return _call("sql-query", [sql])


@mcp.tool
def get_orientation() -> dict:
    """一次调用上手：dispatch 命令图 + run_task 白名单 + 数据目录摘要 + cron 新鲜度 + 关键文档指针。"""
    return _call("orientation")


@mcp.tool
def get_surface_config() -> dict:
    """读盯盘 surface 配置与 resolved 预览（隔夜用户追加 / 指标小卡）。只读；写须走 app 内确认。"""
    return _call("surface-get")


@mcp.tool
def list_surface_metrics() -> dict:
    """指标小卡白名单与当前样例真值。只读。"""
    return _call("surface-metrics")


@mcp.tool
def propose_surface_patch(ops_json: str) -> dict:
    """解析 surface patch 并返回真值预览（不落盘）。ops_json 为 ops 数组 JSON 串。"""
    return _call("surface-propose", [ops_json])


@mcp.tool
def surface_nl_interpret(region: str, text: str) -> dict:
    """档A/B自然语言解析为 surface draft（不落盘）。region=overnight_us|strip_metric；写须 surface-apply + 人确认。"""
    return _call("surface-nl-interpret", [region, text])


@mcp.tool
def surface_catalog(
    slot: str,
    q: str = "",
    market: str = "",
    kind: str = "",
    limit: str = "50",
) -> dict:
    """Bind Catalog 只读搜索。slot=overnight_marquee|strip_metric；q 名/代码；不落盘。"""
    return _call("surface-catalog", [slot, q, market, kind, limit])


@mcp.tool
def list_recipes() -> dict:
    """编排剧本目录(确定性复盘 DAG):每条 name/desc/write/args。选一条用 run_recipe 跑。"""
    return {"recipes": _call("recipe-list")}   # 包 dict:MCP structured_content 须非 list


@mcp.tool
def run_recipe(name: str, args: str = "") -> dict:
    """跑一条只读复盘剧本(如 explain_stock_today)。args 为 JSON 串(如 '{"symbol":"688114.SH"}')。
    只读公开;write 剧本经此拒(write 执行路径 defer 到 #4)。"""
    return _call("run-recipe", [name, args])


@mcp.tool
def research_search(query: str, limit: int = 5) -> dict:
    """搜索外部资料作为 evidence-only 背景；不得覆盖 KSS 本地工具真值。"""
    return _call("research-search", [query, str(limit)])


@mcp.tool
def research_fetch(url: str, max_chars: int = 8000) -> dict:
    """抓取一个外部 URL 的 evidence-only 摘要；带 SSRF 护栏。"""
    return _call("research-fetch", [url, str(max_chars)])


@mcp.tool
def research_bundle(query: str, limit: int = 3, max_chars_per_source: int = 3000) -> dict:
    """搜索并抓取外部证据 bundle，返回 URL/retrievedAt/sourceTier/excerpt ledger。"""
    return _call("research-bundle", [query, str(limit), str(max_chars_per_source)])


@mcp.tool
def get_longbridge_quote(symbol: str) -> dict:
    """Longbridge 实时快照（ChinaConnect LV1，接受延迟）。symbol 形如 688008.SH。

    仅覆盖**陆股通标的**（沪深主板/科创/创业/ETF/指数）；非覆盖或北交所标的返回
    no_realtime_snapshot error。实时数据为 forward_observed（前向观察）、**非 PIT**，
    只用于当日盘面解读，不作回测依据。北交所无实时路径。
    """
    return _call("longbridge-quote", [symbol])


@mcp.tool
def get_longbridge_quotes(symbols: str) -> dict:
    """Longbridge 批量实时快照（单次 SDK 调用）。symbols 逗号分隔，如 688008.SH,510300.SH。

    语义与 get_longbridge_quote 逐标一致：仅陆股通标的有实时；非覆盖/北交所标的
    在 quotes 数组内返回逐标 error。forward_observed、非 PIT。
    """
    return _call("longbridge-quotes", [symbols])


@mcp.tool
def get_indicator_lab() -> dict:
    """指标注册表 + 近期 GO/NO-GO 裁决。"""
    return _call("indicator-lab-list")


@mcp.tool
def backtest_indicator(family: str, params: str, symbols: str = "") -> dict:
    """对一个基元候选跑真数回测 + 五维 GO/NO-GO 裁决（只读，不落地固化）。

    family ∈ {ma_cross, rsi_threshold, boll_atr}；params 为 JSON 串，如
    '{"fast":5,"slow":20,"kind":"sma"}'；symbols 逗号分隔，留空则用自选，单次最多 8 只。
    """
    return _call("indicator-backtest", [family, params, symbols])


@mcp.tool
def suggest_indicator() -> dict:
    """确定性候选建议（代码规则选，不调 LLM）。"""
    return _call("indicator-suggest")


@mcp.tool
def get_intraday_snapshot(symbol: str, interval_minutes: int = 1) -> dict:
    """最新分钟 bar 快照（按覆盖自动选源 longbridge/东财，前向-only）。symbol 形如 688008.SH。

    覆盖标的走 Longbridge 实时；北交所/非陆股通走东财（本机当前可能不可达 = 无数据，
    非错数据）。返回真值 bar 字段（open/high/low/close/volume），forward_observed 非 PIT。
    """
    return _call("intraday-snapshot", [symbol, str(interval_minutes)])


@mcp.tool
def test_datasource(source: str) -> dict:
    """数据源连通性测试（只读，不需 confirm）。source ∈ tushare|longbridge|telegram|llm。

    凭证缺失返回 not_configured（非探测失败）；llm 源对主/备候选分别测试，双结果呈现。
    """
    return _call("datasource-test", [source])


# ---- 写命令（paper-only：仅 KSS_MCP_LIVE=1 注册，且每调用须 confirm）----

if _LIVE:
    @mcp.tool
    def run_task(name: str, confirm: bool = False) -> dict:
        """执行一个数据任务（如 update_data / paper_trade）。须 confirm=True；走 bridge run 白名单。"""
        if not confirm:
            return {"error": "live_write_requires_confirm",
                    "hint": "重调并传 confirm=True 以执行写操作"}
        return _call("run", [name])

    @mcp.tool
    def cron_action(label: str, action: str, confirm: bool = False) -> dict:
        """对计划任务执行 rerun/enable/disable。须 confirm=True；走白名单。"""
        if action not in {"rerun", "enable", "disable"}:
            return {"error": "bad_action", "hint": "action ∈ rerun|enable|disable"}
        if not confirm:
            return {"error": "live_write_requires_confirm", "hint": "传 confirm=True"}
        return _call(f"cron-{action}", [label])

    @mcp.tool
    def cron_edit_schedule(suffix: str, schedule_json: str, confirm: bool = False) -> dict:
        """编辑计划任务排期（写 state-root overlay + 重渲染 + 生效）。须 confirm=True。

        schedule_json 形如 '{"hour":18,"minute":30,"weekdays":[1,2,3,4,5]}'（daily）
        或 '{"weekly":{"weekday":5,"hour":20,"minute":0}}'（weekly）。
        """
        if not confirm:
            return {"error": "live_write_requires_confirm", "hint": "传 confirm=True"}
        return _call("cron-edit-schedule", [suffix, schedule_json])

    @mcp.tool
    def cron_sync(confirm: bool = False) -> dict:
        """把清单与 LaunchAgents 同步（会执行 launchctl bootstrap）。须 confirm=True。"""
        if not confirm:
            return {"error": "live_write_requires_confirm", "hint": "传 confirm=True"}
        return _call("cron-sync")

    @mcp.tool
    def solidify_indicator(
        family: str, params: str, symbols: str, verdict_ref: str = "", confirm: bool = False
    ) -> dict:
        """把已过 GO 门禁的候选固化进注册表+图表+复盘。须 confirm=True；白名单基元族。"""
        if not confirm:
            return {"error": "live_write_requires_confirm", "hint": "重调并传 confirm=True 以执行写操作"}
        return _call("indicator-solidify", [family, params, symbols, verdict_ref])

    @mcp.tool
    def retire_indicator(entry_id: str, confirm: bool = False) -> dict:
        """退役一个已固化指标（不删历史数据）。须 confirm=True。"""
        if not confirm:
            return {"error": "live_write_requires_confirm", "hint": "传 confirm=True"}
        return _call("indicator-retire", [entry_id])


if __name__ == "__main__":
    mcp.run()
