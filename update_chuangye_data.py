#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增量补全创业板 (300/301/302) cs_data_*.csv 数据

现状: cs_data_3*.csv 多数停留在 2025-05-09, 而 cs_data_688*.csv 已到 2026-05-22。
做法: 按 ts_code 调 pro.daily / pro.daily_basic 拉 (last_date+1) ~ end_date 数据,
      与现有 CSV 合并去重写回。
"""

import argparse
import glob
import os
import sys
import time
import logging
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s | %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('upd_cy')

DAILY_BASIC_KEEP = ['ts_code', 'trade_date', 'turnover_rate', 'volume_ratio',
                    'pe', 'pb', 'total_mv']


def get_pro():
    import tushare as ts
    token = os.environ.get('TUSHARE_TOKEN')
    if not token:
        sys.exit('未设置 TUSHARE_TOKEN 环境变量')
    ts.set_token(token)
    return ts.pro_api()


def safe_call(fn, max_retry=4, sleep_on_limit=61, **kw):
    for i in range(max_retry):
        try:
            return fn(**kw)
        except Exception as e:
            msg = str(e)
            if '每分钟最多访问' in msg or 'TooManyRequests' in msg:
                log.warning(f'  限频 ({i+1}/{max_retry}): sleep {sleep_on_limit}s')
                time.sleep(sleep_on_limit)
                continue
            log.error(f'  调用失败 ({i+1}/{max_retry}): {msg[:120]}')
            time.sleep(2)
    raise RuntimeError(f'safe_call exhausted: {fn}')


def last_date_of(csv_path: str) -> str:
    """读 CSV 拿最后一行的 trade_date, 返回 YYYYMMDD 字符串"""
    try:
        df = pd.read_csv(csv_path, usecols=['trade_date'])
        last = df['trade_date'].max()
        return pd.to_datetime(last).strftime('%Y%m%d')
    except Exception:
        return ''


def chuangye_files() -> list:
    files = []
    for fp in sorted(glob.glob(os.path.join(ROOT, 'cs_data_*.csv'))):
        sym = os.path.basename(fp).replace('cs_data_', '').replace('.csv', '')
        if sym.startswith(('300', '301', '302')):
            files.append((sym, fp))
    return files


def fetch_and_merge(pro, sym: str, fp: str, end_date: str) -> tuple:
    last = last_date_of(fp)
    if not last:
        log.warning(f'  {sym} CSV 读不到日期, 跳过')
        return (sym, 0)
    start = (pd.to_datetime(last) + pd.Timedelta(days=1)).strftime('%Y%m%d')
    if start > end_date:
        return (sym, 0)  # 已是最新

    # 上海/深圳后缀
    ts_code = f'{sym}.SZ'  # 创业板全在深交所
    daily = safe_call(pro.daily, ts_code=ts_code, start_date=start, end_date=end_date)
    if daily is None or len(daily) == 0:
        return (sym, 0)
    basic = safe_call(pro.daily_basic, ts_code=ts_code, start_date=start, end_date=end_date)
    if basic is None or len(basic) == 0:
        basic = pd.DataFrame(columns=DAILY_BASIC_KEEP)
    basic = basic[[c for c in DAILY_BASIC_KEEP if c in basic.columns]]

    merged = daily.merge(basic, on=['ts_code', 'trade_date'], how='left')
    merged['trade_date'] = pd.to_datetime(
        merged['trade_date'].astype(str), format='%Y%m%d').dt.strftime('%Y-%m-%d')
    merged = merged.sort_values('trade_date')

    # 合并写回
    old = pd.read_csv(fp)
    full = pd.concat([old, merged], ignore_index=True).drop_duplicates(
        subset=['ts_code', 'trade_date'], keep='last'
    ).sort_values('trade_date')
    full.to_csv(fp, index=False)
    return (sym, len(merged))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--end-date', default='20260522',
                        help='补数到这一天 (含), YYYYMMDD, 默认 20260522')
    parser.add_argument('--limit', type=int, default=None,
                        help='调试用: 只处理前 N 只')
    parser.add_argument('--sleep', type=float, default=0.15,
                        help='每只票之间 sleep 秒数, 默认 0.15')
    args = parser.parse_args()

    pro = get_pro()
    files = chuangye_files()
    if args.limit:
        files = files[:args.limit]
    log.info(f'发现 {len(files)} 只创业板 CSV; 目标补到 {args.end_date}')

    n_updated = 0
    n_rows = 0
    n_skipped = 0
    for i, (sym, fp) in enumerate(files, 1):
        try:
            sym_x, rows = fetch_and_merge(pro, sym, fp, args.end_date)
        except Exception as e:
            log.error(f'  {sym} 失败: {e}')
            continue
        if rows == 0:
            n_skipped += 1
        else:
            n_updated += 1
            n_rows += rows
        if i % 10 == 0:
            log.info(f'  [{i}/{len(files)}] 已更新 {n_updated}, 跳过 {n_skipped}, 新增 {n_rows} 行')
        time.sleep(args.sleep)

    log.info(f'完成 — 更新 {n_updated} 只 / 跳过 {n_skipped} 只 / 总新增 {n_rows} 行')


if __name__ == '__main__':
    main()
