"""预热紫苏叶列表(core+main)个股富化缓存.

遍历注册表 core/main 票，逐只跑 ``enrich`` 把 Tushare(机构/PE) + yFinance(美股对标)
结果写入 kss.db perilla_enrich_cache 表，供 App/MCP 热路径直接命中。

设计：
- 美股侧全墙时整段降级(enrich 内部已处理)，不阻塞 A 股侧缓存。
- 逐票 sleep 限频，避免触发 Tushare 限频。

用法:
    python scripts/refresh_perilla_enrich.py            # 全量 core+main
    python scripts/refresh_perilla_enrich.py --limit 1  # 冒烟(只跑1只)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kss.perilla_enrich import aggregate  # noqa: E402
from kss.supply_chain.registry import ChainRegistry  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="预热紫苏叶个股富化缓存")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 只(冒烟); 0=全量")
    parser.add_argument("--sleep", type=float, default=0.6, help="每只之间休眠秒数(限频)")
    args = parser.parse_args()

    reg = ChainRegistry.from_yaml()
    symbols = [c for c in reg._stocks if reg.tier(c) in ("core", "main")]
    symbols.sort(key=lambda c: -reg.score(c))
    if args.limit > 0:
        symbols = symbols[: args.limit]

    print(f"[*] 预热 {len(symbols)} 只 core+main 富化缓存 ...")
    ok = degraded = 0
    for i, sym in enumerate(symbols, 1):
        out = aggregate.enrich(sym, registry=reg, db_path=aggregate.DB_PATH)
        inst = out.get("institutional", {})
        inst_ok = isinstance(inst, dict) and inst.get("top10", {}).get("status") == "ok"
        pe_ok = out.get("valuation_pe", {}).get("status") == "ok"
        us = out.get("us_peer", {}).get("status", "?")
        flag = "ok" if (inst_ok and pe_ok) else "degraded"
        if flag == "ok":
            ok += 1
        else:
            degraded += 1
        print(f"  [{i}/{len(symbols)}] {sym} {out.get('name','')}: "
              f"机构={'ok' if inst_ok else '—'} PE={'ok' if pe_ok else '—'} 美股={us} [{flag}]")
        if i < len(symbols):
            time.sleep(args.sleep)

    print(f"[*] 完成: {ok} 全绿, {degraded} 部分降级")


if __name__ == "__main__":
    main()
