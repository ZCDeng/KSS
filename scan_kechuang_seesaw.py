#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
科创板「跷跷板 + 机构活跃 + 高弹性 + 多共振」短线候选扫描

打分体系（综合分 = 0.30 A + 0.20 B + 0.25 C + 0.25 D）:
  A. 跷跷板分: 与机器人指数 931494.CSI 的负相关 (近 240 日)，A = (1 - corr) / 2
  B. 机构活跃分: 60 日大单+特大单占成交比，全市场分位归一
  C. 弹性分: 振幅 / 收益波动 / 5%+ 波段次数 三子项分位等权
  D. 共振分: 近 240 日 MACD 金叉 / RSI 超卖反弹 / KDJ 金叉 / 价破 BBI / 大单净流入
            5 信号中 ≥3 同日触发的天数比例

数据源:
  - pro.daily(trade_date=...)         批量日线
  - pro.daily_basic(trade_date=...)   批量 daily_basic
  - pro.moneyflow(trade_date=...)     批量资金流
  - pro.index_daily(ts_code='931494.CSI') 机器人指数

缓存约定 (与项目惯例对齐):
  - cs_data_{symbol}.csv  个股日线+daily_basic
  - mf_{symbol}_SH.csv    资金流
  - idx_931494_CSI.csv    机器人指数
  - _scan_kc_batch_daily.parquet  按日批量缓存
  - _scan_kc_batch_basic.parquet
  - _scan_kc_batch_mf.parquet
"""

import os
import sys
import time
import logging
import warnings
import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

ROOT = os.path.dirname(os.path.abspath(__file__))
END_DATE = '20260522'              # 今天
START_DATE = '20250101'            # 拉一年半，保证 240 日 + 缓冲
WINDOW_240 = 240                   # 跷跷板/共振窗口
WINDOW_60 = 60                     # 弹性/活跃窗口
ROBOT_IDX = '931494.CSI'

BATCH_DAILY = os.path.join(ROOT, '_scan_kc_batch_daily.parquet')
BATCH_BASIC = os.path.join(ROOT, '_scan_kc_batch_basic.parquet')
BATCH_MF = os.path.join(ROOT, '_scan_kc_batch_mf.parquet')


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s | %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('scan_kc')


def get_pro():
    import tushare as ts
    ts.set_token(os.environ['TUSHARE_TOKEN'])
    return ts.pro_api()


def safe_call(fn, *, max_retry=4, sleep_on_limit=61, **kw):
    """对 tushare 限频做 retry。"""
    for i in range(max_retry):
        try:
            return fn(**kw)
        except Exception as e:
            msg = str(e)
            if '每分钟最多访问该接口' in msg or '权限' in msg or 'TooManyRequests' in msg:
                log.warning(f'  限频/权限触发 ({i+1}/{max_retry}): {msg[:80]} → sleep {sleep_on_limit}s')
                time.sleep(sleep_on_limit)
                continue
            log.error(f'  调用失败 ({i+1}/{max_retry}): {msg[:160]}')
            time.sleep(2)
    raise RuntimeError(f'safe_call exhausted: {fn}')


# ──────────────────────────────────────────────────────────────────────────────
# 1. 拉股票列表 + 交易日历
# ──────────────────────────────────────────────────────────────────────────────

def get_stock_basic(pro) -> pd.DataFrame:
    df = safe_call(pro.stock_basic, exchange='', list_status='L',
                   fields='ts_code,symbol,name,industry,market,list_date')
    kc = df[df['ts_code'].str.startswith('688')].copy()
    # 排除 ST
    kc = kc[~kc['name'].str.contains('ST', na=False)]
    # 上市不满 240 天 (按交易日 ~ 360 自然日) 的新股剔除
    end_dt = pd.to_datetime(END_DATE)
    kc['list_date'] = pd.to_datetime(kc['list_date'])
    kc = kc[(end_dt - kc['list_date']).dt.days >= 360].copy()
    kc = kc.reset_index(drop=True)
    log.info(f'科创板 (剔 ST + 剔新股) 候选: {len(kc)} 只')
    return kc


def get_trade_dates(pro, start, end) -> list[str]:
    cal = safe_call(pro.trade_cal, exchange='SSE', start_date=start, end_date=end, is_open='1')
    cal = cal.sort_values('cal_date')
    return cal['cal_date'].tolist()


# ──────────────────────────────────────────────────────────────────────────────
# 2. 批量拉日线/daily_basic/资金流 (按交易日)
# ──────────────────────────────────────────────────────────────────────────────

def fetch_batch_by_date(pro, dates: list[str], cache_path: str, fn_name: str,
                        kch_only: bool = True) -> pd.DataFrame:
    """按交易日批量拉某接口，落 parquet 缓存。每次只补缺日。"""
    if os.path.exists(cache_path):
        existing = pd.read_parquet(cache_path)
        existing['trade_date'] = existing['trade_date'].astype(str)
        have_dates = set(existing['trade_date'].unique())
    else:
        existing = pd.DataFrame()
        have_dates = set()
    need = [d for d in dates if d not in have_dates]
    if not need:
        log.info(f'  [{fn_name}] 缓存命中 全部 {len(dates)} 日')
        return existing
    log.info(f'  [{fn_name}] 需补拉 {len(need)} 日 / 总 {len(dates)} 日')
    fn = getattr(pro, fn_name)
    chunks = [existing] if len(existing) else []
    for i, d in enumerate(need, 1):
        df = safe_call(fn, trade_date=d)
        if df is None or len(df) == 0:
            continue
        if kch_only:
            df = df[df['ts_code'].str.startswith('688')]
        df['trade_date'] = df['trade_date'].astype(str)
        chunks.append(df)
        if i % 20 == 0:
            log.info(f'    [{fn_name}] {i}/{len(need)} 已拉 {d}')
        # 资金流接口限频较严
        if fn_name == 'moneyflow':
            time.sleep(0.12)
        else:
            time.sleep(0.05)
    out = pd.concat(chunks, ignore_index=True)
    out = out.drop_duplicates(subset=['ts_code', 'trade_date'], keep='last')
    out.to_parquet(cache_path, index=False)
    log.info(f'  [{fn_name}] 缓存写入 {cache_path} rows={len(out)}')
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 3. 落 cs_data_{sym}.csv / mf_{sym}_SH.csv 缓存 (与项目惯例一致)
# ──────────────────────────────────────────────────────────────────────────────

def materialize_per_stock_csv(daily: pd.DataFrame, basic: pd.DataFrame, mf: pd.DataFrame,
                              stocks: pd.DataFrame):
    """把批量数据按 ts_code 拆成与项目惯例一致的 csv 文件。"""
    # 合并 daily + basic → cs_data_{sym}.csv
    keep_b = ['ts_code', 'trade_date', 'turnover_rate', 'volume_ratio', 'pe', 'pb', 'total_mv']
    merged = daily.merge(basic[keep_b], on=['ts_code', 'trade_date'], how='left')
    merged['trade_date'] = pd.to_datetime(merged['trade_date'].astype(str), format='%Y%m%d').dt.strftime('%Y-%m-%d')
    merged = merged.sort_values(['ts_code', 'trade_date'])

    written = 0
    for ts_code, g in merged.groupby('ts_code'):
        sym = ts_code.split('.')[0]
        fp = os.path.join(ROOT, f'cs_data_{sym}.csv')
        if os.path.exists(fp):
            # 与现有合并 (现有可能包含 2023 起的更长历史)
            old = pd.read_csv(fp)
            old['trade_date'] = old['trade_date'].astype(str)
            merged_full = pd.concat([old, g], ignore_index=True).drop_duplicates(
                subset=['ts_code', 'trade_date'], keep='last'
            )
            merged_full = merged_full.sort_values('trade_date')
            merged_full.to_csv(fp, index=False)
        else:
            g.to_csv(fp, index=False)
        written += 1
    log.info(f'  cs_data_*.csv 写入 {written} 文件')

    # 资金流
    written = 0
    for ts_code, g in mf.groupby('ts_code'):
        sym = ts_code.split('.')[0]
        fp = os.path.join(ROOT, f'mf_{sym}_SH.csv')
        gg = g.copy()
        gg['trade_date'] = gg['trade_date'].astype(str)
        if os.path.exists(fp):
            old = pd.read_csv(fp, dtype={'trade_date': str})
            gg = pd.concat([old, gg], ignore_index=True).drop_duplicates(
                subset=['ts_code', 'trade_date'], keep='last'
            ).sort_values('trade_date')
        gg.to_csv(fp, index=False)
        written += 1
    log.info(f'  mf_*_SH.csv 写入 {written} 文件')


# ──────────────────────────────────────────────────────────────────────────────
# 4. 指标计算
# ──────────────────────────────────────────────────────────────────────────────

def ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, adjust=False).mean()


def compute_indicators(px: pd.DataFrame) -> pd.DataFrame:
    """传入单只股票按日升序的 dataframe，返回带 macd/rsi14/kdj/bbi 等列。"""
    df = px.sort_values('trade_date').reset_index(drop=True).copy()
    close = df['close']

    # MACD
    e12 = ema(close, 12)
    e26 = ema(close, 26)
    df['dif'] = e12 - e26
    df['dea'] = ema(df['dif'], 9)
    df['macd_hist'] = (df['dif'] - df['dea']) * 2

    # RSI14
    diff = close.diff()
    up = diff.clip(lower=0).rolling(14).mean()
    dn = (-diff.clip(upper=0)).rolling(14).mean()
    rs = up / dn.replace(0, np.nan)
    df['rsi14'] = 100 - 100 / (1 + rs)

    # KDJ (9, 3, 3)
    low9 = df['low'].rolling(9).min()
    high9 = df['high'].rolling(9).max()
    rsv = (close - low9) / (high9 - low9).replace(0, np.nan) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    df['kdj_k'] = k
    df['kdj_d'] = d

    # BBI
    ma3 = close.rolling(3).mean()
    ma6 = close.rolling(6).mean()
    ma12 = close.rolling(12).mean()
    ma24 = close.rolling(24).mean()
    df['bbi'] = (ma3 + ma6 + ma12 + ma24) / 4

    # 日振幅
    df['amp'] = (df['high'] - df['low']) / df['pre_close']
    return df


def compute_signals(df: pd.DataFrame, mf: pd.DataFrame) -> pd.DataFrame:
    """对单只股票产出每日 5 个共振信号。"""
    if len(df) == 0:
        return df
    d = df.copy()
    # D1 MACD 金叉
    d['sig_macd'] = (d['dif'] > d['dea']) & (d['dif'].shift(1) <= d['dea'].shift(1))
    # D2 RSI 超卖反弹
    d['sig_rsi'] = (d['rsi14'] > 30) & (d['rsi14'].shift(1) <= 30)
    # D3 KDJ 金叉
    d['sig_kdj'] = (d['kdj_k'] > d['kdj_d']) & (d['kdj_k'].shift(1) <= d['kdj_d'].shift(1))
    # D4 价破 BBI 上行
    d['sig_bbi'] = (d['close'] > d['bbi']) & (d['close'].shift(1) <= d['bbi'].shift(1))

    # D5 大单+特大单 净流入
    if mf is not None and len(mf) > 0:
        m = mf.copy()
        m['trade_date'] = pd.to_datetime(m['trade_date'].astype(str), format='%Y%m%d').dt.strftime('%Y-%m-%d')
        m['lg_net'] = (m['buy_lg_amount'] + m['buy_elg_amount']) - (m['sell_lg_amount'] + m['sell_elg_amount'])
        d = d.merge(m[['trade_date', 'lg_net']], on='trade_date', how='left')
        d['sig_mf'] = d['lg_net'] > 0
    else:
        d['sig_mf'] = False

    sig_cols = ['sig_macd', 'sig_rsi', 'sig_kdj', 'sig_bbi', 'sig_mf']
    d[sig_cols] = d[sig_cols].fillna(False)
    d['sig_count'] = d[sig_cols].sum(axis=1)
    return d


# ──────────────────────────────────────────────────────────────────────────────
# 5. 4 项得分
# ──────────────────────────────────────────────────────────────────────────────

def load_index(pro, code: str) -> pd.DataFrame:
    cache = os.path.join(ROOT, f'idx_{code.replace(".", "_")}.csv')
    if os.path.exists(cache):
        df = pd.read_csv(cache)
    else:
        df = safe_call(pro.index_daily, ts_code=code, start_date='20230101', end_date=END_DATE)
        df.to_csv(cache, index=False)
    df['trade_date'] = pd.to_datetime(df['trade_date'].astype(str), format='%Y%m%d').dt.strftime('%Y-%m-%d')
    return df[['trade_date', 'pct_chg']].rename(columns={'pct_chg': 'idx_pct'})


def percentile_rank(s: pd.Series) -> pd.Series:
    """0-1 分位 (跳过 NaN)。"""
    return s.rank(pct=True, na_option='keep')


def score_all(stocks_df: pd.DataFrame, daily: pd.DataFrame, basic: pd.DataFrame,
              mf: pd.DataFrame, idx_robot: pd.DataFrame) -> pd.DataFrame:
    """对所有候选股票产出最终评分表。"""
    # 全部转日期字符串 'YYYY-MM-DD'
    daily = daily.copy()
    daily['trade_date'] = pd.to_datetime(daily['trade_date'].astype(str), format='%Y%m%d').dt.strftime('%Y-%m-%d')
    daily = daily.sort_values(['ts_code', 'trade_date'])

    mf = mf.copy()
    mf['trade_date_str'] = pd.to_datetime(mf['trade_date'].astype(str), format='%Y%m%d').dt.strftime('%Y-%m-%d')
    mf['lg_total'] = mf['buy_lg_amount'] + mf['buy_elg_amount'] + mf['sell_lg_amount'] + mf['sell_elg_amount']

    # daily.amount 单位千元；moneyflow 字段单位万元。所以 daily.amount 千元 ÷ 10 = 万元。
    # mf_lg_total 已是万元 ⇒ ratio = mf_lg_total / (daily.amount / 10)
    # 但更稳的方法: 直接用 mf 内部比例 lg_total / (sm+md+lg+elg total)
    mf['mf_all'] = (mf['buy_sm_amount'] + mf['sell_sm_amount']
                    + mf['buy_md_amount'] + mf['sell_md_amount']
                    + mf['buy_lg_amount'] + mf['sell_lg_amount']
                    + mf['buy_elg_amount'] + mf['sell_elg_amount'])
    mf['lg_ratio_internal'] = mf['lg_total'] / mf['mf_all'].replace(0, np.nan)

    # 也算 vs daily.amount 的口径 (要 merge 上 daily)
    mf_with_amt = mf.merge(
        daily[['ts_code', 'trade_date', 'amount']].rename(columns={'trade_date': 'trade_date_str'}),
        on=['ts_code', 'trade_date_str'], how='left'
    )
    # daily.amount 千元 / 10 = 万元
    mf_with_amt['lg_ratio_amt'] = mf_with_amt['lg_total'] / (mf_with_amt['amount'] / 10).replace(0, np.nan)

    # ── 60 日窗口 (最近 60 个交易日)
    last_dates = sorted(daily['trade_date'].unique())[-WINDOW_60:]
    mf_60 = mf_with_amt[mf_with_amt['trade_date_str'].isin(last_dates)]

    # B 机构活跃分: 取每股 60d 均 ratio_internal (更稳)，再加 ratio_amt 的截尾平均做备选
    b_int = mf_60.groupby('ts_code')['lg_ratio_internal'].mean()
    b_amt = mf_60.groupby('ts_code')['lg_ratio_amt'].apply(lambda s: s.dropna().clip(0, 1).mean())
    b_score_raw = (b_int.fillna(0) * 0.5 + b_amt.fillna(0) * 0.5)

    # ── 跷跷板 A: corr 与 robot index, 取近 240 个交易日
    idx_robot = idx_robot.set_index('trade_date')['idx_pct']
    dates_240 = sorted(daily['trade_date'].unique())[-WINDOW_240:]

    a_scores = {}
    elas_c1 = {}  # 振幅
    elas_c2 = {}  # 收益波动
    elas_c3 = {}  # 5%+ 次数

    daily_60 = daily[daily['trade_date'].isin(last_dates)]
    daily_240 = daily[daily['trade_date'].isin(dates_240)]

    for ts_code, g in daily_240.groupby('ts_code'):
        g = g.sort_values('trade_date')
        if len(g) < 120:   # 样本不足
            continue
        idx_align = idx_robot.reindex(g['trade_date']).values
        stk_pct = g['pct_chg'].values
        mask = ~(np.isnan(idx_align) | np.isnan(stk_pct))
        if mask.sum() < 120:
            continue
        corr = np.corrcoef(stk_pct[mask], idx_align[mask])[0, 1]
        if np.isnan(corr):
            continue
        a_scores[ts_code] = (1 - corr) / 2

    # 弹性子项 - 60d
    for ts_code, g in daily_60.groupby('ts_code'):
        g = g.sort_values('trade_date')
        if len(g) < 40:
            continue
        # C1 振幅
        amp = ((g['high'] - g['low']) / g['pre_close']).mean()
        # C2 20d 收益波动 (这里直接用 60d std of pct_chg %)
        ret_std = g['pct_chg'].std()
        # C3 5%+ 波段次数
        big = (g['pct_chg'].abs() >= 5).sum()
        elas_c1[ts_code] = amp
        elas_c2[ts_code] = ret_std
        elas_c3[ts_code] = big

    # D 共振分: 5 信号 ≥3 同日, 占 240 日比例
    d_scores = {}
    extra_signals_last5 = {}  # 用于报告: 最近 5 日触发情况
    for ts_code, g in daily.groupby('ts_code'):
        g = g.sort_values('trade_date')
        if len(g) < 60:
            continue
        ind = compute_indicators(g)
        mf_one = mf[mf['ts_code'] == ts_code][[
            'trade_date_str', 'buy_lg_amount', 'buy_elg_amount',
            'sell_lg_amount', 'sell_elg_amount'
        ]].rename(columns={'trade_date_str': 'trade_date'})
        mf_one_for_sig = mf_one.rename(columns={'trade_date': 'trade_date_old'})
        mf_one_for_sig['trade_date'] = mf_one_for_sig['trade_date_old']
        # compute_signals 期望 mf 的 trade_date 列是 'YYYYMMDD' 然后 to_datetime
        # 这里直接给已转字符串 'YYYY-MM-DD'
        d_ind = compute_indicators(g)
        # 自己写一份适配版的 merge (跳过 compute_signals 内部的格式假设)
        d_ind['sig_macd'] = (d_ind['dif'] > d_ind['dea']) & (d_ind['dif'].shift(1) <= d_ind['dea'].shift(1))
        d_ind['sig_rsi'] = (d_ind['rsi14'] > 30) & (d_ind['rsi14'].shift(1) <= 30)
        d_ind['sig_kdj'] = (d_ind['kdj_k'] > d_ind['kdj_d']) & (d_ind['kdj_k'].shift(1) <= d_ind['kdj_d'].shift(1))
        d_ind['sig_bbi'] = (d_ind['close'] > d_ind['bbi']) & (d_ind['close'].shift(1) <= d_ind['bbi'].shift(1))
        if len(mf_one) > 0:
            mf_one['lg_net'] = (mf_one['buy_lg_amount'] + mf_one['buy_elg_amount']) - (
                mf_one['sell_lg_amount'] + mf_one['sell_elg_amount']
            )
            d_ind = d_ind.merge(mf_one[['trade_date', 'lg_net']], on='trade_date', how='left')
            d_ind['sig_mf'] = d_ind['lg_net'].fillna(0) > 0
        else:
            d_ind['sig_mf'] = False

        sig_cols = ['sig_macd', 'sig_rsi', 'sig_kdj', 'sig_bbi', 'sig_mf']
        d_ind[sig_cols] = d_ind[sig_cols].fillna(False)
        d_ind['sig_count'] = d_ind[sig_cols].sum(axis=1)

        # 近 240 天
        tail = d_ind.tail(WINDOW_240)
        valid = tail.dropna(subset=['rsi14', 'kdj_k', 'bbi'])
        if len(valid) < 60:
            continue
        d_scores[ts_code] = (valid['sig_count'] >= 3).mean()
        # 收 5 日明细给 top1 用
        last5 = d_ind.tail(5)[['trade_date', 'close', 'sig_macd', 'sig_rsi', 'sig_kdj', 'sig_bbi', 'sig_mf', 'sig_count']]
        extra_signals_last5[ts_code] = last5

    # 汇总
    rows = []
    for ts_code in stocks_df['ts_code']:
        if ts_code not in a_scores:
            continue
        if ts_code not in elas_c1:
            continue
        if ts_code not in d_scores:
            continue
        rows.append({
            'ts_code': ts_code,
            'A_corr': a_scores[ts_code],
            'B_raw': b_score_raw.get(ts_code, np.nan),
            'C1_amp': elas_c1[ts_code],
            'C2_std': elas_c2[ts_code],
            'C3_big': elas_c3[ts_code],
            'D_reson': d_scores[ts_code],
        })
    sc = pd.DataFrame(rows)
    if len(sc) == 0:
        return sc

    # 全市场分位归一
    sc['B_score'] = percentile_rank(sc['B_raw']).fillna(0)
    sc['C1_p'] = percentile_rank(sc['C1_amp']).fillna(0)
    sc['C2_p'] = percentile_rank(sc['C2_std']).fillna(0)
    sc['C3_p'] = percentile_rank(sc['C3_big']).fillna(0)
    sc['C_score'] = (sc['C1_p'] + sc['C2_p'] + sc['C3_p']) / 3
    sc['A_score'] = sc['A_corr'].clip(0, 1)
    sc['D_score'] = sc['D_reson'].clip(0, 1)

    sc['final'] = 0.30 * sc['A_score'] + 0.20 * sc['B_score'] + 0.25 * sc['C_score'] + 0.25 * sc['D_score']

    # 合 name / industry
    sc = sc.merge(stocks_df[['ts_code', 'name', 'industry']], on='ts_code', how='left')

    # 拿最近收盘价 / 涨跌
    last_daily = daily.sort_values('trade_date').groupby('ts_code').tail(1)
    sc = sc.merge(
        last_daily[['ts_code', 'trade_date', 'close', 'pct_chg']],
        on='ts_code', how='left'
    )
    sc = sc.sort_values('final', ascending=False).reset_index(drop=True)
    sc.attrs['signals_last5'] = extra_signals_last5
    return sc


# ──────────────────────────────────────────────────────────────────────────────
# 6. 报告生成
# ──────────────────────────────────────────────────────────────────────────────

def build_report(sc: pd.DataFrame, signals_last5: dict, out_path: str):
    top = sc.head(10).copy()
    rank_map = {ts: i + 1 for i, ts in enumerate(sc['ts_code'])}

    def fmt_signal_row(r):
        flags = []
        for col, label in [('sig_macd', 'MACD'), ('sig_rsi', 'RSI'),
                           ('sig_kdj', 'KDJ'), ('sig_bbi', 'BBI'), ('sig_mf', '大单')]:
            if r.get(col, False):
                flags.append(label)
        return f"{r['trade_date']} close={r['close']:.2f} 触发={'/'.join(flags) if flags else '—'} (n={int(r['sig_count'])})"

    lines = []
    lines.append('# 科创板「跷跷板 + 机构活跃 + 高弹性 + 多共振」短线候选扫描报告')
    lines.append('')
    lines.append(f'- 扫描日期: 2026-05-22')
    lines.append(f'- 候选池: 科创板 (剔除 ST、上市不满 1 年新股)')
    lines.append(f'- 跷跷板基准: 机器人指数 931494.CSI')
    lines.append(f'- 综合分公式: `0.30*A + 0.20*B + 0.25*C + 0.25*D`')
    lines.append(f'- 入榜候选数 (3 维数据齐全): **{len(sc)}** 只')
    lines.append('')
    lines.append('## 一、Top 10 候选明细')
    lines.append('')
    lines.append('| 排名 | 代码 | 名称 | 行业 | 收盘 | 涨跌% | 综合分 | A 跷跷板 | B 机构活跃 | C 弹性 | D 共振 |')
    lines.append('|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|')
    for i, r in top.iterrows():
        lines.append(
            f"| {i+1} | {r['ts_code']} | {r['name']} | {r['industry'] or '-'} | "
            f"{r['close']:.2f} | {r['pct_chg']:+.2f} | **{r['final']:.3f}** | "
            f"{r['A_score']:.3f} (corr={1-2*r['A_score']:+.2f}) | {r['B_score']:.3f} | "
            f"{r['C_score']:.3f} | {r['D_score']:.3f} |"
        )
    lines.append('')

    # ── Top 1 详细介绍
    t1 = top.iloc[0]
    lines.append(f'## 二、Top 1 推荐 · {t1["ts_code"]} {t1["name"]}')
    lines.append('')
    lines.append(f'- **综合分**: {t1["final"]:.3f}')
    lines.append(f'- 行业: {t1["industry"] or "-"}')
    lines.append(f'- 最新收盘 ({t1["trade_date"]}): {t1["close"]:.2f}, 当日 {t1["pct_chg"]:+.2f}%')
    lines.append('')
    lines.append('### 4 项分项打分')
    lines.append('')
    lines.append('| 维度 | 分项 | 数值 | 原始指标 |')
    lines.append('|---|---|---:|---|')
    lines.append(f'| A 跷跷板 | 与 931494.CSI 负相关程度 | {t1["A_score"]:.3f} | corr = {1 - 2*t1["A_score"]:+.3f} |')
    lines.append(f'| B 机构活跃 | 60d 大单占成交比 (分位) | {t1["B_score"]:.3f} | 原始均值 = {t1["B_raw"]:.3f} |')
    lines.append(f'| C1 弹性·振幅 | 60d 日均振幅 (分位) | {t1["C1_p"]:.3f} | {t1["C1_amp"]*100:.2f}% |')
    lines.append(f'| C2 弹性·波动 | 60d 收益 σ (分位) | {t1["C2_p"]:.3f} | {t1["C2_std"]:.2f}% |')
    lines.append(f'| C3 弹性·急涨跌 | 5%+ 单日次数 (分位) | {t1["C3_p"]:.3f} | {int(t1["C3_big"])} 次 |')
    lines.append(f'| D 共振 | 5 信号 ≥3 同日比例 | {t1["D_score"]:.3f} | 240d 中 {t1["D_reson"]*240:.0f} 天 |')
    lines.append('')
    lines.append('### 最近 5 个交易日信号触发')
    lines.append('')
    if t1['ts_code'] in signals_last5:
        for _, r in signals_last5[t1['ts_code']].iterrows():
            lines.append(f'- {fmt_signal_row(r)}')
    lines.append('')
    lines.append('### 选它的理由')
    reasons = []
    if t1['A_score'] >= 0.6:
        reasons.append(f'**跷跷板属性突出**: 与机器人指数日度相关 {1 - 2*t1["A_score"]:+.2f}, '
                       f'A 分位 {t1["A_score"]:.2f}, 大盘机器人板块调整时可对冲。')
    if t1['B_score'] >= 0.6:
        reasons.append(f'**机构活跃**: 60d 大单+特大单占总成交全市场分位 {t1["B_score"]:.2f}, '
                       f'有持续主动资金参与。')
    if t1['C_score'] >= 0.6:
        reasons.append(f'**高弹性**: 60d 日均振幅 {t1["C1_amp"]*100:.2f}%, 收益 σ={t1["C2_std"]:.2f}%, '
                       f'出现 {int(t1["C3_big"])} 次 5%+ 单日; 综合弹性分位 {t1["C_score"]:.2f}。')
    if t1['D_score'] >= 0.2:
        reasons.append(f'**共振历史**: 近 240 日中有 {t1["D_reson"]*240:.0f} 天出现 ≥3 个信号同时触发, '
                       f'共振分位 {t1["D_score"]:.2f}, 短线击发概率高。')
    for x in reasons:
        lines.append(f'- {x}')
    lines.append('')

    # ── Top 2-3 备选
    lines.append('## 三、Top 2-3 备选')
    lines.append('')
    for idx in [1, 2]:
        r = top.iloc[idx]
        lines.append(f'### #{idx+1} {r["ts_code"]} {r["name"]} (综合 {r["final"]:.3f})')
        lines.append(f'- 行业: {r["industry"] or "-"} | 收盘 {r["close"]:.2f} ({r["pct_chg"]:+.2f}%)')
        lines.append(f'- A {r["A_score"]:.2f} (corr={1-2*r["A_score"]:+.2f}) · '
                     f'B {r["B_score"]:.2f} · C {r["C_score"]:.2f} · D {r["D_score"]:.2f}')
        lines.append('')

    # ── 与 688322/688017 对比
    lines.append('## 四、对照: 688322 (奥比中光) / 688017 (绿的谐波) 排名')
    lines.append('')
    targets = [('688322.SH', '奥比中光'), ('688017.SH', '绿的谐波')]
    lines.append('| 代码 | 名称 | 综合排名 | 综合分 | A | B | C | D |')
    lines.append('|---|---|---:|---:|---:|---:|---:|---:|')
    for ts, nm in targets:
        if ts in rank_map:
            r = sc[sc['ts_code'] == ts].iloc[0]
            lines.append(f'| {ts} | {nm} | **{rank_map[ts]}** / {len(sc)} | {r["final"]:.3f} | '
                         f'{r["A_score"]:.3f} | {r["B_score"]:.3f} | {r["C_score"]:.3f} | {r["D_score"]:.3f} |')
        else:
            lines.append(f'| {ts} | {nm} | 未入榜 (数据不足) | - | - | - | - | - |')
    lines.append('')

    # ── 方法学
    lines.append('## 五、方法学说明')
    lines.append('')
    lines.append('- **A 跷跷板分** `(1 - corr)/2`, corr 为近 240 日个股 pct_chg 与 931494.CSI 的 Pearson')
    lines.append('- **B 机构活跃分** 取 60 日 (a) `lg_total / mf_total_internal` 和 (b) `lg_total / (amount/10)` 各 50%, 再全市场分位归一')
    lines.append('- **C 弹性分** = (60d 日均振幅分位 + 60d pct_chg σ 分位 + 60d 5%+ 次数分位) / 3')
    lines.append('- **D 共振分** = 近 240 日中 5 个二值信号 (MACD/RSI/KDJ/BBI/大单) ≥ 3 个同日触发的天数比例')
    lines.append('- 排除: ST、上市不满 360 自然日、数据缺失 > 30% 的股票')
    lines.append('')

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────

def main():
    pro = get_pro()

    log.info('1) 拉股票列表 + 交易日历')
    stocks = get_stock_basic(pro)
    dates = get_trade_dates(pro, START_DATE, END_DATE)
    log.info(f'  交易日 {len(dates)} 个: {dates[0]} ~ {dates[-1]}')

    # 用最近 250 个交易日 (覆盖 240 窗口 + 缓冲)
    use_dates = dates[-250:]
    log.info(f'  使用最近 {len(use_dates)} 个交易日: {use_dates[0]} ~ {use_dates[-1]}')

    log.info('2) 批量拉 daily / daily_basic / moneyflow (按交易日)')
    daily = fetch_batch_by_date(pro, use_dates, BATCH_DAILY, 'daily')
    basic = fetch_batch_by_date(pro, use_dates, BATCH_BASIC, 'daily_basic')
    mf = fetch_batch_by_date(pro, use_dates, BATCH_MF, 'moneyflow')

    # 只保留候选股
    cands = set(stocks['ts_code'])
    daily = daily[daily['ts_code'].isin(cands)].copy()
    basic = basic[basic['ts_code'].isin(cands)].copy()
    mf = mf[mf['ts_code'].isin(cands)].copy()
    log.info(f'  过滤后 daily={len(daily)} basic={len(basic)} mf={len(mf)}')

    log.info('3) 写 cs_data_*.csv / mf_*_SH.csv 缓存 (与项目惯例一致)')
    materialize_per_stock_csv(daily, basic, mf, stocks)

    log.info('4) 加载机器人指数 931494.CSI')
    idx_robot = load_index(pro, ROBOT_IDX)
    log.info(f'  指数行数 {len(idx_robot)}')

    log.info('5) 计算 4 项得分')
    sc = score_all(stocks, daily, basic, mf, idx_robot)
    log.info(f'  入榜 {len(sc)} 只 (3 维数据齐全)')

    log.info('6) 输出报告')
    out_md = os.path.join(ROOT, 'scan_kechuang_seesaw_report.md')
    build_report(sc, sc.attrs.get('signals_last5', {}), out_md)
    out_csv = os.path.join(ROOT, 'scan_kechuang_seesaw_rank.csv')
    sc.to_csv(out_csv, index=False)

    log.info('=' * 70)
    log.info(f'报告: {out_md}')
    log.info(f'排名: {out_csv}')
    log.info(f'脚本: {os.path.abspath(__file__)}')
    log.info('=' * 70)

    # 控制台预览
    print('\n──── Top 10 ────')
    cols = ['ts_code', 'name', 'industry', 'final', 'A_score', 'B_score', 'C_score', 'D_score']
    print(sc[cols].head(10).to_string(index=False))

    # 对比目标
    print('\n──── 对照 688322 / 688017 ────')
    for ts in ['688322.SH', '688017.SH']:
        if ts in sc['ts_code'].values:
            row = sc[sc['ts_code'] == ts].iloc[0]
            rank = sc[sc['ts_code'] == ts].index[0] + 1
            print(f"  {ts} {row['name']}: rank={rank}/{len(sc)} final={row['final']:.3f} "
                  f"A={row['A_score']:.3f} B={row['B_score']:.3f} C={row['C_score']:.3f} D={row['D_score']:.3f}")
        else:
            print(f"  {ts}: 未入榜")


if __name__ == '__main__':
    main()
