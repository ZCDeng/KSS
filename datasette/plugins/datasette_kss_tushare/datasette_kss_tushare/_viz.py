"""Server-side matplotlib rendering for KSS charts that datasette-agent-charts
can't do (candlestick OHLC, stacked four-tier moneyflow).

Returns a base64 PNG embedded in <img> — no JS bundle, no static asset, no
client-side dependency. Plot is a snapshot at render time (vs charts plugin,
which re-runs SQL on every page load).

Public API (used by __init__.py):
    candlestick_html(df, title) -> str
    moneyflow_stack_html(df, title) -> str
"""

from __future__ import annotations

import base64
import io

import matplotlib
matplotlib.use("Agg")  # no GUI
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402


# CJK font fallback chain. Picked at import time, not per-render, so renders
# stay fast. If none of these are installed matplotlib falls back to its
# default and Chinese characters render as boxes — acceptable for a dev tool.
_CJK_FONTS = ["PingFang SC", "Heiti SC", "STHeiti", "Arial Unicode MS",
              "Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["font.sans-serif"] = _CJK_FONTS
plt.rcParams["axes.unicode_minus"] = False


def _fig_to_data_uri(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f'<img src="data:image/png;base64,{b64}" style="max-width:100%">'


def candlestick_html(df, title: str = "") -> str:
    """Render a candlestick chart. df needs columns: trade_date, open, high, low, close.

    Optional `vol` column adds a volume sub-panel.

    Chinese convention: 涨=红, 跌=绿 (inverse of US).
    """
    required = {"trade_date", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        return (f'<p><em>candlestick needs columns {sorted(required)}, '
                f'missing {sorted(missing)}</em></p>')
    if len(df) == 0:
        return '<p><em>candlestick: no rows</em></p>'

    df = df.sort_values("trade_date").reset_index(drop=True)
    has_vol = "vol" in df.columns

    if has_vol:
        fig, (ax, ax_vol) = plt.subplots(
            2, 1, figsize=(11, 6), sharex=True,
            gridspec_kw={"height_ratios": [3, 1], "hspace": 0.05},
        )
    else:
        fig, ax = plt.subplots(figsize=(11, 5))
        ax_vol = None

    width = 0.6
    for i, row in df.iterrows():
        o, c, h, l = row["open"], row["close"], row["high"], row["low"]
        up = c >= o
        color = "#c0392b" if up else "#27ae60"
        ax.plot([i, i], [l, h], color=color, linewidth=0.8, zorder=2)
        body_bottom = min(o, c)
        body_height = abs(c - o) or (h - l) * 0.001  # doji floor
        ax.add_patch(Rectangle(
            (i - width / 2, body_bottom), width, body_height,
            facecolor=color, edgecolor=color, zorder=3,
        ))
        if ax_vol is not None:
            ax_vol.bar(i, row["vol"], width=width, color=color, alpha=0.6)

    ax.set_xlim(-0.8, len(df) - 0.2)
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    ax.set_ylabel("price")
    if title:
        ax.set_title(title)

    n_ticks = min(8, len(df))
    tick_idx = [int(i) for i in
                [j * (len(df) - 1) / (n_ticks - 1) for j in range(n_ticks)]] \
        if n_ticks > 1 else [0]
    labels = [str(df.iloc[i]["trade_date"]) for i in tick_idx]
    target_ax = ax_vol if ax_vol is not None else ax
    target_ax.set_xticks(tick_idx)
    target_ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    if ax_vol is not None:
        ax_vol.grid(axis="y", linestyle=":", alpha=0.4)
        ax_vol.set_ylabel("vol")

    return _fig_to_data_uri(fig)


def moneyflow_stack_html(df, title: str = "") -> str:
    """Render four-tier moneyflow as a stacked bar chart.

    df needs columns: trade_date + at least one of
    {buy_sm_amount, sell_sm_amount, buy_md_amount, sell_md_amount,
     buy_lg_amount, sell_lg_amount, buy_elg_amount, sell_elg_amount}.
    Net per tier = buy - sell. Tiers stacked, x = trade_date.
    """
    if len(df) == 0:
        return '<p><em>moneyflow: no rows</em></p>'
    df = df.sort_values("trade_date").reset_index(drop=True)

    tiers = [("sm", "小单", "#85c1e9"),
             ("md", "中单", "#5dade2"),
             ("lg", "大单", "#2e86c1"),
             ("elg", "超大单", "#1b4f72")]

    fig, ax = plt.subplots(figsize=(11, 5))
    x = range(len(df))
    bottom_pos = [0.0] * len(df)
    bottom_neg = [0.0] * len(df)
    legend_done = []

    for prefix, label, color in tiers:
        buy_col = f"buy_{prefix}_amount"
        sell_col = f"sell_{prefix}_amount"
        if buy_col not in df.columns or sell_col not in df.columns:
            continue
        net = (df[buy_col] - df[sell_col]).tolist()
        bottoms = [bottom_pos[i] if net[i] >= 0 else bottom_neg[i] + net[i]
                   for i in range(len(net))]
        ax.bar(list(x), net, bottom=bottoms, color=color, label=label, width=0.7)
        for i, v in enumerate(net):
            if v >= 0:
                bottom_pos[i] += v
            else:
                bottom_neg[i] += v
        legend_done.append(label)

    ax.axhline(0, color="#444", linewidth=0.6)
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    ax.set_ylabel("net flow (万元)")
    if title:
        ax.set_title(title)
    if legend_done:
        ax.legend(loc="upper left", fontsize=8)

    n_ticks = min(8, len(df))
    tick_idx = [int(i) for i in
                [j * (len(df) - 1) / (n_ticks - 1) for j in range(n_ticks)]] \
        if n_ticks > 1 else [0]
    ax.set_xticks(tick_idx)
    ax.set_xticklabels([str(df.iloc[i]["trade_date"]) for i in tick_idx],
                       rotation=30, ha="right", fontsize=8)

    return _fig_to_data_uri(fig)
