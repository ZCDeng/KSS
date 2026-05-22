"""Background-agent goal templates + spawner for KSS anomaly scans.

Mirrors datasette_agent.explorer.start_explorer's pattern: instead of asking
the user to type a multi-paragraph goal into the spawn dialog, we generate it
from a few structured parameters so the agent gets a consistent runbook.
"""

from __future__ import annotations

import json
from typing import Any


def make_anomaly_goal(
    criteria: str,
    anchor_date: str | None = None,
    lookback_days: int = 5,
    top_n: int = 10,
) -> str:
    """Build a 5-step KSS anomaly-scan goal. Pure — easy to unit test."""
    anchor_clause = (
        f"锚日固定为 {anchor_date}（紧凑格式 YYYYMMDD）。"
        if anchor_date
        else "锚日：先 SELECT max(trade_date) FROM kc_daily 取最新交易日（紧凑格式 YYYYMMDD）。"
    )
    return (
        "你是 KSS 科创板异动扫描 agent。在 sqlite 库 kss 上工作。\n\n"
        f"筛选条件（用户描述）：{criteria}\n"
        f"{anchor_clause}\n"
        f"回看天数：{lookback_days}\n"
        f"最多展开：{top_n} 个候选\n\n"
        "数据布局（必读）：\n"
        "- kc_daily(ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount)\n"
        "- kc_basic(ts_code, trade_date, close, turnover_rate, volume_ratio, pe, pe_ttm, pb, total_mv, circ_mv, ...)\n"
        "- kc_moneyflow(ts_code, trade_date, buy_sm_amount, sell_sm_amount, "
        "buy_md_amount, sell_md_amount, buy_lg_amount, sell_lg_amount, "
        "buy_elg_amount, sell_elg_amount, net_mf_amount, net_mf_vol)\n"
        "- 三表 (ts_code, trade_date) 同主键可 JOIN，trade_date 均为 'YYYYMMDD'\n"
        "- net_mf_amount 单位 万元；amount 单位 千元\n\n"
        "执行步骤（每步用 sql_query；发现 1 个候选就立刻 append_to_report 写一段，不要憋到最后）：\n"
        "1. 确定锚日 → append_to_report 一行"
        "（'锚日 YYYYMMDD，候选池大小 N'）\n"
        "2. 跑筛选 SQL：基于条件 + 锚日附近 lookback 天，找候选 ts_code 列表\n"
        "3. 对前 top_n 个候选，每个查 30 日 (close, vol, net_mf_amount) 序列，"
        "在报告里附 markdown 表格 + 一句话点评：\n"
        "   - 标的代码 + 触发条件实际数值\n"
        "   - 30 日量价趋势（放量/缩量、突破/震荡）\n"
        "   - 30 日资金流方向（净流入/净流出累计）\n"
        "4. 汇总段：候选总数、按净流入强度 top 5 排名、有无共性（行业/估值）\n"
        "5. 调 mark_finished({final_message: '扫描完成，发现 N 个候选'})\n\n"
        "约束：\n"
        "- SQL 用 sql_query 的 display='model'（rows 给你做判断，用户读报告即可）\n"
        "- 单次 SQL 加 LIMIT 防止爆量\n"
        "- 任何一步失败 → mark_finished({error: '...'}) 不要无限重试\n"
    )


async def spawn_kss_anomaly_scan(
    datasette, actor,
    criteria: str,
    anchor_date: str | None = None,
    lookback_days: int = 5,
    top_n: int = 10,
) -> str:
    """Spawn a background scan; return agent_id + conversation_id."""
    from datasette_agent.api import start_background_agent

    goal = make_anomaly_goal(criteria, anchor_date, lookback_days, top_n)
    agent_id = await start_background_agent(
        datasette=datasette,
        actor=actor,
        goal=goal,
    )
    payload: dict[str, Any] = {
        "agent_id": agent_id,
        "criteria": criteria,
        "anchor_date": anchor_date,
        "lookback_days": lookback_days,
        "top_n": top_n,
        "_html": (
            f'<p>📡 KSS 异动扫描已启动 — agent_id <code>{agent_id}</code>。'
            f'调用 <code>check_background_agent(agent_id="{agent_id}")</code> 看进度，'
            f'或访问 <a href="/-/agent/background">/-/agent/background</a>。</p>'
        ),
    }
    return json.dumps(payload, ensure_ascii=False)
