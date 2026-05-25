#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
当日组合信号扫描 (OOS 动态校准版)

每次启动:
  1. 构建 RPS 面板 + 两板 top-30 池
  2. 对每个候选组合, 实时算近 4 个半年窗口的 5d 收益, 给 ✓/△/✗ 判定
  3. 跑当日全样本扫描, 列出触发该组合的票

判定 (近 4 个半年: 2024H2, 2025H1, 2025H2, 2026H1):
  入场组合 ✓ 仍有效  : ≥75% 窗口 5d 均 > 0 且最差窗口 > -1%
  入场组合 △ 减弱     : ≥50% 窗口 5d 均 > 0 且均值仍 > 0
  入场组合 ✗ 已失效   : 其余 (2025+ 已反向, 不应作为入场参考)

  避雷组合 ✓ 仍是避雷 : 所有窗口 lift_vs_base < 0 且 50%+ 窗口绝对值为负
  避雷组合 △ 部分有效 : ≥50% 窗口 lift_vs_base < 0
  避雷组合 ✗ 已失效   : 其余

数据源: 本地已存的 cs_data_*.csv (与 backtest_pattern_match.py 共用)
"""

import argparse
import glob
import os
import sys
import pandas as pd

from backtest_pattern_match import (
    load, add_indicators,
    pattern_macd_extreme, pattern_volume_burst, pattern_consecutive_big_up,
    build_rps_panel, attach_mtf, ROOT, NAMES,
)
from pool_mtf_spread import pick_top_n_by_mv, collect_pool_triggers


# storage/macro/regime_daily.parquet — 由 scripts/backfill_regime_history.py
# 与 scripts/update_macro_daily.py 维护；这里只读，缺文件时静默降级.
_REGIME_PARQUET_PATH = os.path.join(ROOT, 'storage', 'macro', 'regime_daily.parquet')
_VALUATION_PARQUET_PATH = os.path.join(ROOT, 'storage', 'macro', 'valuation_n_daily.parquet')


def _lookup_regime(trade_date_str: str) -> dict | None:
    """thin wrapper over :func:`kss.macro.queries.lookup_regime`.

    保留 dict 接口供 build_telegram_message / banner 用，最小改动 caller 代码.
    Plan 010 (#19) 把实际查询逻辑提到 kss.macro.queries.
    """
    try:
        from kss.macro.queries import lookup_regime
    except ImportError:
        return None
    info = lookup_regime(str(trade_date_str), max_stale_days=3)
    return info.as_dict() if info is not None else None


def _modulate_entry_count(stage: str | None, requested: int) -> int:
    """thin wrapper over :func:`kss.macro.regime.modulate_entry_count_by_stage`
    (plan 010 #17). 保留旧名字以兼容现有 caller.
    """
    from kss.macro.regime import modulate_entry_count_by_stage
    return modulate_entry_count_by_stage(stage, requested)


def _apply_risk_prefilters(symbols: list) -> tuple[list, dict | None]:
    """对候选 symbols 跑 ST / 高杠杆 / 低流动性 三道硬过滤.

    数据源策略（缺什么就跳过对应过滤器，不抛错）：

    - ``storage/stock_names.csv`` → ST 名 + industry_map
    - ``storage/macro/stock_basic.parquet``（可选缓存） → 覆盖 delist_date
    - ``storage/macro/fina_quarterly.parquet``（可选缓存） → debt_to_assets + 连亏
    - ``cs_data_<sym>.csv`` → 流动性窗口

    Returns:
        ``(kept_symbols, summary_dict)``. summary_dict 含 ``n_removed`` /
        ``counts``（按 reason 聚合）/ ``examples`` (每类前 3 个示例 ts_code).
        无任何过滤数据时返回 ``(symbols, None)``.
    """
    try:
        from kss.strategies.risk_filters import (
            apply_all_filters,
            daily_amounts_from_cs_data,
            load_fina_cache,
            load_stock_basic_cache,
            summarize_removed,
        )
    except ImportError:
        return symbols, None

    # symbols 在 scan_combo_signals 通常是裸码 (688322)。stock_names.csv
    # 用 ts_code 带后缀格式 (688322.SH)。统一在内部用 ts_code 后缀格式比对，
    # 比对失败时降级处理.
    sym_to_ts: dict[str, str] = {}
    stock_basic_df = None
    industry_map: dict[str, str] = {}
    names_csv = os.path.join(ROOT, 'storage', 'stock_names.csv')
    if os.path.exists(names_csv):
        try:
            names = pd.read_csv(names_csv, usecols=['ts_code', 'name', 'industry'])
            names['code'] = names['ts_code'].astype(str).str.split('.').str[0]
            for _, r in names.iterrows():
                sym_to_ts[str(r['code'])] = str(r['ts_code'])
                industry_map[str(r['ts_code'])] = str(r.get('industry', '') or '')
            stock_basic_df = names[['ts_code', 'name']].copy()
            stock_basic_df['delist_date'] = None
        except Exception:
            stock_basic_df = None

    # stock_basic 缓存（覆盖 delist_date 与权威 name）
    cached_basic = load_stock_basic_cache()
    if cached_basic is not None and not cached_basic.empty:
        stock_basic_df = cached_basic

    # 财务季频缓存（无则跳过 leverage + 连亏检查）
    fina_panel = load_fina_cache()

    # 用 ts_code 后缀格式跑过滤；symbol_in_to_ts 缺失时退化用原 code
    ts_symbols = [sym_to_ts.get(s, str(s)) for s in symbols]

    # 流动性数据用裸码 key，apply 时映射
    daily_amounts_naked = daily_amounts_from_cs_data(symbols)
    daily_amounts = {sym_to_ts.get(k, k): v for k, v in daily_amounts_naked.items()}

    result = apply_all_filters(
        ts_symbols,
        fina_panel=fina_panel,
        industry_map=industry_map if industry_map else None,
        daily_amounts=daily_amounts if daily_amounts else None,
        stock_basic=stock_basic_df,
    )
    if not result.removed:
        return symbols, {'n_removed': 0, 'counts': {}, 'examples': {}}

    removed_ts = {r['ts_code'] for r in result.removed}
    ts_to_sym = {v: k for k, v in sym_to_ts.items()}
    kept = [s for s in symbols if sym_to_ts.get(s, s) not in removed_ts]

    examples: dict[str, list[str]] = {}
    for r in result.removed:
        examples.setdefault(r['reason'], []).append(ts_to_sym.get(r['ts_code'], r['ts_code']))
    summary = {
        'n_removed': len(result.removed),
        'counts': summarize_removed(result.removed),
        'examples': {k: v[:3] for k, v in examples.items()},
    }
    return kept, summary


def _lookup_valuation(trade_date_str: str) -> dict | None:
    """thin wrapper over :func:`kss.macro.queries.lookup_valuation` (plan 010 #19)."""
    try:
        from kss.macro.queries import lookup_valuation
    except ImportError:
        return None
    info = lookup_valuation(str(trade_date_str), percentile_window=1260)
    return info.as_dict() if info is not None else None


def _lookup_rotation(stage: str | None) -> dict | None:
    """thin wrapper over :func:`kss.macro.queries.lookup_rotation` (plan 010 #19)."""
    try:
        from kss.macro.queries import lookup_rotation
    except ImportError:
        return None
    info = lookup_rotation(stage)
    return info.as_dict() if info is not None else None


def _load_full_names() -> dict:
    """加载 storage/stock_names.csv 全市场票名映射 (code → name)"""
    fp = os.path.join(ROOT, 'storage', 'stock_names.csv')
    if not os.path.exists(fp):
        return {}
    try:
        df = pd.read_csv(fp, usecols=['ts_code', 'name'])
        df['code'] = df['ts_code'].str.split('.').str[0]
        return dict(zip(df['code'], df['name']))
    except Exception:
        return {}


# CSV 在前, NAMES 在后 — NAMES 是用户手动 curated 别名, 优先级更高
NAMES_FULL = {**_load_full_names(), **NAMES}

PATTERNS = {
    'P1': ('MACD柱≥P95',     pattern_macd_extreme,        {'q_th': 0.95}),
    'P2': ('量能≥P95',        pattern_volume_burst,        {'q_th': 0.95}),
    'P3': ('5d≥2根放量大阳',   pattern_consecutive_big_up,  {'min_cnt': 2}),
}

HALVES = [
    ('2024H2', '2024-07-01', '2024-12-31'),
    ('2025H1', '2025-01-01', '2025-06-30'),
    ('2025H2', '2025-07-01', '2025-12-31'),
    ('2026H1', '2026-01-01', '2026-12-31'),
]


def _rps_5080(d):
    r = d['rps_w_lag'].fillna(-1)
    return (r >= 50) & (r < 80)


def _b(d, col):
    return d[col].fillna(False).astype(bool)


# 候选入场组合 (定义保留, 是否仍有效由 OOS 动态判定)
ENTRY_COMBOS = [
    {
        'name': 'P3 + RPS50-80 + W趋势↓ + OBV>EMA20',
        'pattern': 'P3',
        'cond': lambda d: _rps_5080(d) & (~_b(d, 'w_trend_up')) & _b(d, 'obv_above_ema'),
    },
    {
        'name': 'P3 + RPS50-80 + W趋势↓',
        'pattern': 'P3',
        'cond': lambda d: _rps_5080(d) & (~_b(d, 'w_trend_up')),
    },
    {
        'name': 'P2 + RPS50-80 + W趋势↓ + OBV>EMA20',
        'pattern': 'P2',
        'cond': lambda d: _rps_5080(d) & (~_b(d, 'w_trend_up')) & _b(d, 'obv_above_ema'),
    },
    {
        'name': 'P3 + OBV顶背离 + W趋势↓ (反转入场)',
        'pattern': 'P3',
        'cond': lambda d: _b(d, 'obv_top_div') & (~_b(d, 'w_trend_up')),
    },
    {
        'name': 'P3 + RPS50-80 + RSI<70',
        'pattern': 'P3',
        'cond': lambda d: _rps_5080(d) & (~_b(d, 'rsi_overbought')),
    },
]

# 候选避雷组合 (是否仍有效由 OOS 动态判定)
AVOID_COMBOS = [
    {
        'name': 'W趋势↑ + OBV顶背离 (顶部避雷)',
        'patterns': ['P2', 'P3'],
        'cond': lambda d: _b(d, 'w_trend_up') & _b(d, 'obv_top_div'),
    },
    {
        'name': 'W趋势↑ + RPS≥80 (双重过热)',
        'patterns': ['P1', 'P2', 'P3'],
        'cond': lambda d: _b(d, 'w_trend_up') & (d['rps_w_lag'].fillna(-1) >= 80),
    },
    {
        'name': '多指标顶背离 + W趋势↑ (顶部共振)',
        'patterns': ['P1', 'P2', 'P3'],
        'cond': lambda d: _b(d, 'multi_top_div') & _b(d, 'w_trend_up'),
    },
    {
        'name': 'OBV量价确认 + W趋势↑ (P2 量能诱多)',
        'patterns': ['P2'],
        'cond': lambda d: _b(d, 'obv_confirm_up') & _b(d, 'w_trend_up'),
    },
]


def detect_board(sym: str) -> str:
    if sym.startswith('688'):
        return '科创'
    if sym.startswith(('300', '301', '302')):
        return '创业'
    return '其他'


def scan_universe(board: str = 'all') -> list:
    files = sorted(glob.glob(os.path.join(ROOT, 'cs_data_*.csv')))
    out = []
    for fp in files:
        sym = os.path.basename(fp).replace('cs_data_', '').replace('.csv', '')
        if board == 'kechuang' and not sym.startswith('688'):
            continue
        if board == 'chuangye' and not sym.startswith(('300', '301', '302')):
            continue
        out.append(sym)
    return out


def evaluate_today(symbols: list, rps_panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sym in symbols:
        try:
            df = add_indicators(load(sym))
            df = attach_mtf(df, sym, rps_panel)
        except Exception:
            continue
        if len(df) < 60:
            continue
        last = df.iloc[-1:].copy().assign(sym=sym)
        for pcode, (_, pfn, kwargs) in PATTERNS.items():
            last[f'trig_{pcode}'] = pfn(df, **kwargs).iloc[-1]
        rows.append(last)
    if not rows:
        return pd.DataFrame()
    today_df = pd.concat(rows, ignore_index=True)
    max_date = today_df['trade_date'].max()
    return today_df[today_df['trade_date'] == max_date].reset_index(drop=True)


# ──────────────────────────────────────────────────────────────────────────────
# OOS 动态计算 + banner 生成
# ──────────────────────────────────────────────────────────────────────────────

def compute_combo_oos_for_pool(pool: dict, pcode: str, cond_fn, min_n: int = 5) -> list:
    """返回近 4 个半年窗口的 [{'label', 'n', 'm5'}]"""
    df_pat = pool.get(pcode)
    if df_pat is None or df_pat.empty:
        return [{'label': l, 'n': 0, 'm5': None} for l, _, _ in HALVES]
    sub = df_pat[cond_fn(df_pat)]
    out = []
    for label, t0, t1 in HALVES:
        win = sub[(sub['trade_date'] >= t0) & (sub['trade_date'] <= t1)]
        s = win['fwd_5d'].dropna()
        n = len(s)
        m5 = s.mean() if n >= min_n else None
        out.append({'label': label, 'n': n, 'm5': m5})
    return out


def entry_verdict(oos: list) -> str:
    valid = [r['m5'] for r in oos if r['m5'] is not None]
    if not valid:
        return '— 样本不足'
    pos_ratio = sum(1 for m in valid if m > 0) / len(valid)
    worst = min(valid)
    mean_m = sum(valid) / len(valid)
    if pos_ratio >= 0.75 and worst > -0.01:
        return '✓ 仍有效'
    if pos_ratio >= 0.5 and mean_m > 0:
        return '△ 减弱'
    return '✗ 已失效'


def avoid_verdict(oos_combo: list, oos_base: list) -> str:
    pairs = [(c, b) for c, b in zip(oos_combo, oos_base)
             if c['m5'] is not None and b['m5'] is not None]
    if not pairs:
        return '— 样本不足'
    lifts = [c['m5'] - b['m5'] for c, b in pairs]
    neg_combo_ratio = sum(1 for c, _ in pairs if c['m5'] < 0) / len(pairs)
    if all(l < 0 for l in lifts) and neg_combo_ratio >= 0.5:
        return '✓ 仍是避雷'
    if sum(l < 0 for l in lifts) / len(lifts) >= 0.5:
        return '△ 部分有效'
    return '✗ 已失效'


def format_oos_line(oos: list, board_tag: str) -> str:
    parts = []
    for r in oos:
        if r['m5'] is None:
            parts.append(f"{r['label']}:n{r['n']}小")
        else:
            parts.append(f"{r['label']}:{r['m5']*100:+.1f}%/n{r['n']}")
    return f"{board_tag} " + ' '.join(parts)


def fmt_stock_row(r, extra=None):
    sym = r['sym']
    name = NAMES_FULL.get(sym, '')
    rps = r['rps_w_lag']
    rps_s = f'{rps:.0f}' if pd.notna(rps) else 'NA'
    wtu = '↑' if bool(r['w_trend_up']) else '↓'
    base = (f"     {sym:<10}{detect_board(sym):<5}{name:<10}"
            f"{rps_s:>4}  W{wtu}  收{r['close']:>7.2f}  "
            f"{r['pct_chg']:+5.2f}%  振{r['high']/r['low']-1:>5.1%}")
    if extra:
        base += '  ' + extra
    return base


def collect_entry_hits(today_df: pd.DataFrame, kc_pool: dict, cy_pool: dict) -> list:
    """返回 [{combo, marker, kc_v, cy_v, kc_oos, cy_oos, hit_df}], 仅 OOS 至少一板非✗失效"""
    out = []
    for combo in ENTRY_COMBOS:
        pcode = combo['pattern']
        cond = combo['cond']
        cond_mask = cond(today_df)
        trig_mask = today_df[f'trig_{pcode}'].astype(bool)
        hit = today_df[trig_mask & cond_mask].copy()
        kc_oos = compute_combo_oos_for_pool(kc_pool, pcode, cond)
        cy_oos = compute_combo_oos_for_pool(cy_pool, pcode, cond)
        kc_v = entry_verdict(kc_oos)
        cy_v = entry_verdict(cy_oos)
        if hit.empty and kc_v == '✗ 已失效' and cy_v == '✗ 已失效':
            continue
        marker = '▲' if ('✓' in kc_v or '✓' in cy_v) else (
                 '△' if ('△' in kc_v or '△' in cy_v) else '◯')
        out.append({
            'combo': combo, 'pcode': pcode, 'marker': marker,
            'kc_v': kc_v, 'cy_v': cy_v,
            'kc_oos': kc_oos, 'cy_oos': cy_oos,
            'hit': hit,
        })
    return out


def top_n_picks(entry_hits: list, top_n: int,
                regime_stage: str | None = None) -> pd.DataFrame:
    """对所有入场命中去重 + 按 "✓>△ → rotation_score → n_combo → 市值" 排序, 取 top N.

    Args:
        entry_hits: :func:`collect_entry_hits` 输出.
        top_n: 取前 N 条.
        regime_stage: 当前 Bolton 周期阶段（``I/II/III/IV``）；
            非 ``None`` 时按 :func:`kss.macro.rotation.score_industry_fit` ×0.2
            算 ``rotation_score`` 加入排序键（plan 007 #41）；``None`` 时退回原行为.

    Returns:
        排序后 DataFrame；``regime_stage`` 非空时附 ``rotation_score`` 列.
    """
    all_hits = []
    for h in entry_hits:
        for _, r in h['hit'].iterrows():
            verdict = h['kc_v'] if r['sym'].startswith('688') else h['cy_v']
            all_hits.append({
                'sym': r['sym'],
                'combo_name': h['combo']['name'],
                'pcode': h['pcode'],
                'verdict': verdict,
                'close': r['close'],
                'pct_chg': r['pct_chg'],
                'high': r['high'], 'low': r['low'],
                'rps': r.get('rps_w_lag'),
                'w_trend_up': r.get('w_trend_up'),
                'mv': r.get('total_mv'),
            })
    if not all_hits:
        return pd.DataFrame()
    df = pd.DataFrame(all_hits)
    agg = df.groupby('sym').agg(
        n_combo=('combo_name', 'count'),
        combos=('combo_name', lambda s: ' / '.join(sorted(set(s)))),
        verdicts=('verdict', lambda s: '|'.join(sorted(set(s)))),
        close=('close', 'first'),
        pct_chg=('pct_chg', 'first'),
        high=('high', 'first'),
        low=('low', 'first'),
        rps=('rps', 'first'),
        w_trend_up=('w_trend_up', 'first'),
        mv=('mv', 'first'),
    ).reset_index()
    # 优先级: ✓ > △
    agg['has_check'] = agg['verdicts'].str.contains('✓').astype(int)

    # 部门轮换打分 (plan 007 #41)：行业 ↔ 阶段适配 × 0.2 权重
    if regime_stage:
        try:
            from kss.macro.rotation import load_industry_map, score_industry_fit
        except ImportError:
            agg = agg.sort_values(['has_check', 'n_combo', 'mv'],
                                  ascending=[False, False, False]).head(top_n)
            return agg.drop(columns=['has_check'])

        industry_map = load_industry_map()
        # sym 是 6 位裸码，industry_map key 是 ts_code 带后缀；做后缀拼装
        def _score_for(sym: str) -> float:
            code = str(sym)
            for suffix in ('.SH', '.SZ', '.BJ'):
                ts = f'{code}{suffix}'
                if ts in industry_map:
                    return score_industry_fit(industry_map[ts], regime_stage) * 0.2
            return 0.0

        agg['rotation_score'] = agg['sym'].apply(_score_for)
        agg = agg.sort_values(
            ['has_check', 'rotation_score', 'n_combo', 'mv'],
            ascending=[False, False, False, False],
        ).head(top_n)
        return agg.drop(columns=['has_check'])

    agg = agg.sort_values(['has_check', 'n_combo', 'mv'],
                          ascending=[False, False, False]).head(top_n)
    return agg.drop(columns=['has_check'])


def report_entry(today_df: pd.DataFrame, kc_pool: dict, cy_pool: dict,
                 top_entry: int = 5, regime_stage: str | None = None):
    entry_hits = collect_entry_hits(today_df, kc_pool, cy_pool)
    picks = top_n_picks(entry_hits, top_entry, regime_stage=regime_stage)

    print('\n' + '=' * 100)
    print(f'  ▲ 当日入场候选 Top-{top_entry} (按"匹配组合数 + 市值"排序, ✓现状判定优先)')
    print('=' * 100)
    if picks.empty:
        print('\n  (今日全样本无任何入场候选触发)')
    else:
        print(f"     {'#':<3}{'代码':<9}{'板':<5}{'名称':<10}{'mv亿':<7}{'RPS':<5}{'W':<3}"
              f"{'收盘':<9}{'当日%':<8}{'振幅':<7}{'匹配组合数':<8}{'判定':<10}")
        print('     ' + '-' * 92)
        for i, (_, r) in enumerate(picks.iterrows(), 1):
            sym = r['sym']
            wtu = '↑' if bool(r['w_trend_up']) else '↓'
            mv_yi = (r['mv'] / 10000) if pd.notna(r['mv']) else None
            amp = r['high'] / r['low'] - 1 if r['low'] > 0 else 0
            mv_str = f'{mv_yi:.0f}' if mv_yi else '--'
            rps_str = f"{r['rps']:.0f}" if pd.notna(r['rps']) else '--'
            print(f"     {i:<3}{sym:<9}{detect_board(sym):<5}{NAMES_FULL.get(sym, ''):<10}"
                  f"{mv_str:<7}{rps_str:<5}{wtu:<3}"
                  f"{r['close']:<9.2f}{r['pct_chg']:+6.2f}%  {amp:>5.1%}  "
                  f"{int(r['n_combo']):<8}{r['verdicts']:<10}")
            print(f"        命中: {r['combos']}")

    print('\n' + '─' * 100)
    print('  ▼ 各组合明细 (含 OOS 现状)')
    print('─' * 100)
    for h in entry_hits:
        print(f"\n  {h['marker']} [{h['pcode']}] {h['combo']['name']}")
        print(f"     {format_oos_line(h['kc_oos'], '科创')} → {h['kc_v']}")
        print(f"     {format_oos_line(h['cy_oos'], '创业')} → {h['cy_v']}")
        if h['hit'].empty:
            print('     (今日无触发)')
            continue
        for _, r in h['hit'].iterrows():
            print(fmt_stock_row(r))
    return picks, entry_hits


def collect_avoid_hits(today_df: pd.DataFrame, kc_pool: dict, cy_pool: dict) -> list:
    """返回 [{combo, kc_v, cy_v, hit_df}], 仅至少一板非✗失效"""
    out = []
    for combo in AVOID_COMBOS:
        cond = combo['cond']
        cond_mask = cond(today_df)
        trig_mask = pd.Series(False, index=today_df.index)
        for pcode in combo['patterns']:
            trig_mask |= today_df[f'trig_{pcode}'].astype(bool)
        hit = today_df[trig_mask & cond_mask].copy()
        main_pcode = combo['patterns'][0]
        kc_oos = compute_combo_oos_for_pool(kc_pool, main_pcode, cond)
        cy_oos = compute_combo_oos_for_pool(cy_pool, main_pcode, cond)
        kc_base = compute_combo_oos_for_pool(kc_pool, main_pcode,
                                              lambda d: pd.Series(True, index=d.index))
        cy_base = compute_combo_oos_for_pool(cy_pool, main_pcode,
                                              lambda d: pd.Series(True, index=d.index))
        kc_v = avoid_verdict(kc_oos, kc_base)
        cy_v = avoid_verdict(cy_oos, cy_base)
        if hit.empty and kc_v == '✗ 已失效' and cy_v == '✗ 已失效':
            continue
        out.append({
            'combo': combo, 'main_pcode': main_pcode,
            'kc_v': kc_v, 'cy_v': cy_v,
            'kc_oos': kc_oos, 'cy_oos': cy_oos,
            'kc_base': kc_base, 'cy_base': cy_base,
            'hit': hit,
        })
    return out


def report_avoid(today_df: pd.DataFrame, kc_pool: dict, cy_pool: dict):
    avoid_hits = collect_avoid_hits(today_df, kc_pool, cy_pool)
    print('\n' + '=' * 100)
    print('  ✗ 当日避雷清单 (附 OOS 近 4 半年 vs 基准对比 + 现状判定)')
    print('=' * 100)
    if not avoid_hits or all(h['hit'].empty for h in avoid_hits):
        print('\n  (今日全样本无避雷信号触发)')
    for h in avoid_hits:
        combo = h['combo']
        marker = '✗' if ('✓' in h['kc_v'] or '✓' in h['cy_v']) else (
                 '△' if ('△' in h['kc_v'] or '△' in h['cy_v']) else '◯')
        print(f"\n  {marker} {combo['name']}  [主 pattern={h['main_pcode']}]")
        print(f"     {format_oos_line(h['kc_oos'], '科创组合')} → {h['kc_v']}")
        print(f"     {format_oos_line(h['kc_base'], '科创基准')}")
        print(f"     {format_oos_line(h['cy_oos'], '创业组合')} → {h['cy_v']}")
        print(f"     {format_oos_line(h['cy_base'], '创业基准')}")
        if h['hit'].empty:
            print('     (今日无触发)')
            continue
        for _, r in h['hit'].iterrows():
            trigs = '+'.join([p for p in combo['patterns'] if r[f'trig_{p}']])
            print(fmt_stock_row(r, extra=f'触发 {trigs}'))
    return avoid_hits


def report_unmatched_triggers(today_df: pd.DataFrame, show: int = 20):
    print('\n' + '=' * 100)
    print(f'  ◯ 当日 P1/P2/P3 触发但未匹配甜区/避雷组合 (前 {show} 只)')
    print('=' * 100)
    any_trig = (today_df['trig_P1'].astype(bool) | today_df['trig_P2'].astype(bool)
                | today_df['trig_P3'].astype(bool))
    sub = today_df[any_trig].copy()
    if sub.empty:
        print('\n  (无)')
        return
    sub['trigs'] = sub.apply(
        lambda r: ''.join([p for p in ['P1', 'P2', 'P3'] if r[f'trig_{p}']]), axis=1)
    sub = sub.sort_values('pct_chg', ascending=False).head(show)
    print('     ' + '-' * 92)
    for _, r in sub.iterrows():
        print(fmt_stock_row(r, extra=f"触发 {r['trigs']}"))


def build_telegram_message(today_df: pd.DataFrame, picks: pd.DataFrame,
                            avoid_hits: list, last_date,
                            regime: dict | None = None,
                            rotation_hint: dict | None = None,
                            risk_summary: dict | None = None,
                            valuation: dict | None = None) -> str:
    lines = []
    lines.append(f'📊 <b>组合扫描 {last_date.strftime("%Y-%m-%d")}</b>')
    if regime:
        lines.append(
            f"<i>宏观阶段 {regime['stage']} (置信度 {regime['confidence']:.2f})</i>"
        )
    if valuation:
        pct_str = (f", 5Y 分位 {valuation['percentile_5y']*100:.0f}%"
                   if valuation['percentile_5y'] is not None else '')
        pe_str = f"{valuation['pe']:.1f}" if valuation.get('pe') is not None else 'N/A'
        n_str = (f"{valuation['n_years']:.1f}"
                 if valuation.get('n_years') is not None else 'N/A')
        lines.append(
            f"<i>HS300 PE={pe_str} n={n_str} "
            f"[{valuation['rule']}]{pct_str}</i>"
        )
    if rotation_hint:
        prefs = ' / '.join(rotation_hint['preferred'][:5]) or '—'
        avoid = ' / '.join(rotation_hint['avoid'][:5]) or '—'
        lines.append(f"<i>优先板块: {prefs}</i>")
        lines.append(f"<i>回避板块: {avoid}</i>")
    if risk_summary and risk_summary.get('n_removed'):
        parts = ' / '.join(f"{k} {v}" for k, v in risk_summary['counts'].items())
        lines.append(f"<i>风险过滤剔除 {risk_summary['n_removed']} 只 ({parts})</i>")
    lines.append(f'<i>当日触发 P1={today_df["trig_P1"].sum()} '
                 f'P2={today_df["trig_P2"].sum()} '
                 f'P3={today_df["trig_P3"].sum()}</i>')
    lines.append('')
    lines.append(f'▲ <b>入场 Top {len(picks) if not picks.empty else 0}</b>')
    if picks.empty:
        lines.append('  无候选')
    else:
        for i, (_, r) in enumerate(picks.iterrows(), 1):
            sym = r['sym']
            name = NAMES_FULL.get(sym, '')
            wtu = '↑' if bool(r['w_trend_up']) else '↓'
            mv_yi = (r['mv'] / 10000) if pd.notna(r['mv']) else None
            rps_s = f"{r['rps']:.0f}" if pd.notna(r['rps']) else '--'
            mv_s = f"{mv_yi:.0f}亿" if mv_yi else '--'
            name_part = f' {name}' if name else ''
            lines.append(
                f"{i}. <b>{sym}{name_part}</b> "
                f"{r['pct_chg']:+.2f}% RPS{rps_s} W{wtu} {mv_s} "
                f"<i>{int(r['n_combo'])}组合命中</i>"
            )
    lines.append('')
    lines.append('✗ <b>避雷 (持仓自查)</b>')
    shown = 0
    for h in avoid_hits:
        if h['hit'].empty:
            continue
        if h['kc_v'] == '✗ 已失效' and h['cy_v'] == '✗ 已失效':
            continue
        cname = h['combo']['name']
        syms = h['hit']['sym'].tolist()
        top = syms[:8]
        more = len(syms) - len(top)
        v_tag = '✓' if ('✓' in h['kc_v'] or '✓' in h['cy_v']) else '△'
        lines.append(f"• <b>{cname}</b> [{v_tag}] {len(syms)}只")
        lines.append(f"  {' '.join(top)}" + (f' +{more}' if more > 0 else ''))
        shown += 1
        if shown >= 4:
            break
    if shown == 0:
        lines.append('  无')
    lines.append('')
    lines.append('<i>console 日志: scan_combo_signals.py</i>')
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--board', choices=['all', 'kechuang', 'chuangye'], default='all')
    parser.add_argument('--limit', type=int, default=None,
                        help='调试用: 限制当日扫描股票数')
    parser.add_argument('--oos-pool-size', type=int, default=30,
                        help='OOS 测算用的两板 top-N 池规模 (默认 30)')
    parser.add_argument('--top-entry', type=int, default=5,
                        help='入场候选展示数 (默认 5)')
    parser.add_argument('--show-unmatched', action='store_true',
                        help='列出已触发 P1/P2/P3 但未落到任何组合的票')
    parser.add_argument('--channel', choices=['console', 'telegram', 'all'],
                        default='console', help='输出通道 (telegram 推送精简版 Top-N)')
    parser.add_argument('--dry-run', action='store_true',
                        help='跳过推送, 仅 console (telegram 模式下用)')
    parser.add_argument('--skip-risk-filter', action='store_true',
                        help='跳过个股财务/流动性硬过滤 (默认开启)')
    args = parser.parse_args()

    print('[*] 构建周线 RPS 横截面面板 ...', end='', flush=True)
    rps_panel = build_rps_panel(ROOT)
    print(f' 完成 ({rps_panel.shape[1]} 票 × {len(rps_panel)} 周)')

    print(f'[*] 构建 OOS 测算池 (两板 top-{args.oos_pool_size}) ...', end='', flush=True)
    kc_syms = pick_top_n_by_mv(ROOT, args.oos_pool_size, board='kechuang')
    cy_syms = pick_top_n_by_mv(ROOT, args.oos_pool_size, board='chuangye')
    kc_pool, _ = collect_pool_triggers(kc_syms, rps_panel)
    cy_pool, _ = collect_pool_triggers(cy_syms, rps_panel)
    print(f' 完成 (科创 P3={len(kc_pool["P3"])}, 创业 P3={len(cy_pool["P3"])})')

    symbols = scan_universe(args.board)
    if args.limit:
        symbols = symbols[:args.limit]
    print(f'[*] 当日扫描 (板块={args.board}) — {len(symbols)} 只')

    # 风险前过滤：ST / 高杠杆 / 低流动性
    risk_summary: dict | None = None
    if not args.skip_risk_filter:
        symbols, risk_summary = _apply_risk_prefilters(symbols)
        if risk_summary:
            parts = ', '.join(f"{k}={v}" for k, v in risk_summary['counts'].items())
            print(f"[*] 风险过滤后剩 {len(symbols)} 只 (剔除 {risk_summary['n_removed']} 只: {parts})")

    print('[*] 计算当日指标 + 形态 + MTF ...', end='', flush=True)
    today_df = evaluate_today(symbols, rps_panel)
    print(f' 完成 ({len(today_df)} 只有效)')

    if today_df.empty:
        print('无有效数据')
        return

    last_date = today_df['trade_date'].iloc[0]
    print(f'\n  数据截至: {last_date.date()}')
    print(f'  OOS 窗口: {HALVES[0][0]} ~ {HALVES[-1][0]}  '
          f'(每窗口最少 5 个样本才统计 5d 均值)')
    print(f'  当日 P1/P2/P3 触发数: '
          f"P1={today_df['trig_P1'].sum()}  "
          f"P2={today_df['trig_P2'].sum()}  "
          f"P3={today_df['trig_P3'].sum()}")

    # 宏观阶段标签 — 缺数据时 regime=None，行为与未集成时一致
    regime = _lookup_regime(last_date.strftime('%Y%m%d'))
    regime_top = _modulate_entry_count(
        regime['stage'] if regime else None, args.top_entry,
    )
    rotation_hint = _lookup_rotation(regime['stage'] if regime else None)

    # 估值 n —— 与 regime 独立计算，合成 effective_top_entry.
    # Fix #7: 不能简单 min() — 否则 valuation 'cool' (扩到 7) 永远被 regime 收紧覆盖.
    # 规则：(a) 任一为 0 → 全程 suppress；(b) 两者均扩 (>requested) → max；
    # (c) 一扩一缩 → 收紧优先 (用 min 但保证 <=requested 的方向)；(d) 都收缩 → min.
    valuation = _lookup_valuation(last_date.strftime('%Y%m%d'))
    if valuation:
        from kss.macro.valuation import modulate_entry_count as _val_mod
        val_top = _val_mod(valuation['rule'], args.top_entry)
        if regime_top == 0 or val_top == 0:
            effective_top_entry = 0
        elif regime_top > args.top_entry and val_top > args.top_entry:
            effective_top_entry = max(regime_top, val_top)
        else:
            # 至少有一边在收缩 → 取收缩方
            effective_top_entry = min(regime_top, val_top)
    else:
        effective_top_entry = regime_top

    if regime:
        adj = ''
        if effective_top_entry != args.top_entry:
            adj = f' → entry 候选 {args.top_entry}→{effective_top_entry}'
        stale_warn = ' ⚠️ STALE' if regime.get('stale') else ''
        print(
            f"  宏观阶段: {regime['stage']} "
            f"(置信度 {regime['confidence']:.2f}, as_of {regime['as_of']}){stale_warn}{adj}"
        )
        if rotation_hint:
            prefs = ' / '.join(rotation_hint['preferred'][:5]) or '—'
            avoid = ' / '.join(rotation_hint['avoid'][:5]) or '—'
            print(f"  本阶段优先: {prefs}")
            print(f"  本阶段回避: {avoid}")
    else:
        print('  宏观阶段: 无 regime_daily.parquet (跳过)')

    if valuation:
        pct_str = (f", 5Y 分位 {valuation['percentile_5y']*100:.0f}%"
                   if valuation['percentile_5y'] is not None else '')
        pe_str = f"{valuation['pe']:.2f}" if valuation.get('pe') is not None else 'N/A'
        n_str = (f"{valuation['n_years']:.2f}"
                 if valuation.get('n_years') is not None else 'N/A')
        print(
            f"  HS300 估值: PE={pe_str} n={n_str} "
            f"[{valuation['rule']}]{pct_str}"
        )
    else:
        print('  HS300 估值: 无 valuation_n_daily.parquet (跳过)')

    picks, _ = report_entry(
        today_df, kc_pool, cy_pool, top_entry=effective_top_entry,
        regime_stage=regime['stage'] if regime else None,
    )
    avoid_hits = report_avoid(today_df, kc_pool, cy_pool)
    if args.show_unmatched:
        report_unmatched_triggers(today_df)
    print()

    # Telegram 推送 (精简 Top-N 版本)
    if args.channel in ('telegram', 'all') and not args.dry_run:
        try:
            from kss.notifications.manager import send_to_channels
        except Exception as e:
            print(f'[警告] 无法导入 kss.notifications: {e}', file=sys.stderr)
            return
        msg = build_telegram_message(
            today_df, picks, avoid_hits, last_date,
            regime=regime, rotation_hint=rotation_hint,
            risk_summary=risk_summary, valuation=valuation,
        )
        results = send_to_channels(
            message=msg,
            channel='telegram' if args.channel == 'telegram' else 'all',
            title=f'组合扫描 {last_date.strftime("%Y-%m-%d")}',
            parse_mode='HTML',
        )
        print(f'[*] 推送结果: {results}')
        if not all(results.values()):
            sys.exit(2)


if __name__ == '__main__':
    main()
