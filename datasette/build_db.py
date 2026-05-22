"""Build kss.db from cs_data CSVs and the kc_batch parquet files.

Usage:
    python build_db.py [--rebuild]

Tables produced:
    daily          - all cs_data_*.csv merged (root + cs_data/)
    kc_daily       - _scan_kc_batch_daily.parquet
    kc_basic       - _scan_kc_batch_basic.parquet
    kc_moneyflow   - _scan_kc_batch_mf.parquet
"""

from __future__ import annotations

import argparse
import glob
import sqlite3
from pathlib import Path

import pandas as pd

KSS_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(__file__).resolve().parent / "kss.db"


def load_cs_data() -> pd.DataFrame:
    paths = sorted(set(glob.glob(str(KSS_ROOT / "cs_data_*.csv"))
                       + glob.glob(str(KSS_ROOT / "cs_data" / "cs_data_*.csv"))))
    if not paths:
        raise SystemExit("no cs_data_*.csv found under KSS root")
    frames = [pd.read_csv(p) for p in paths]
    df = pd.concat(frames, ignore_index=True)
    df["trade_date"] = df["trade_date"].astype(str)
    df = df.drop_duplicates(subset=["ts_code", "trade_date"])
    print(f"cs_data: {len(paths)} files, {len(df)} rows after dedup")
    return df


def load_parquet(name: str) -> pd.DataFrame | None:
    p = KSS_ROOT / f"_scan_kc_batch_{name}.parquet"
    if not p.exists():
        print(f"skip {p.name} (missing)")
        return None
    df = pd.read_parquet(p)
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].astype(str)
    print(f"{p.name}: {len(df)} rows")
    return df


def write(conn: sqlite3.Connection, name: str, df: pd.DataFrame, key_cols: list[str]):
    df.to_sql(name, conn, if_exists="replace", index=False)
    cols = ", ".join(key_cols)
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{name}_keys ON {name} ({cols})")
    print(f"  wrote table {name} ({len(df)} rows)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()

    if DB_PATH.exists() and args.rebuild:
        DB_PATH.unlink()
    print(f"target: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    try:
        write(conn, "daily", load_cs_data(), ["ts_code", "trade_date"])
        if (kd := load_parquet("daily")) is not None:
            write(conn, "kc_daily", kd, ["ts_code", "trade_date"])
        if (kb := load_parquet("basic")) is not None:
            write(conn, "kc_basic", kb, ["ts_code"])
        if (km := load_parquet("mf")) is not None:
            write(conn, "kc_moneyflow", km, ["ts_code", "trade_date"])
        conn.commit()
    finally:
        conn.close()
    print("done.")


if __name__ == "__main__":
    main()
