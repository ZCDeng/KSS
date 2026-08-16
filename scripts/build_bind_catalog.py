#!/usr/bin/env python3
"""生成 STATE_ROOT/storage/ui_surface/bind_catalog_v{CATALOG_VERSION}.json。

文件名跟着 bind_catalog.CATALOG_VERSION 走：bump 版本后旧文件自然失效、不再被读。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    p = argparse.ArgumentParser(description="Build bind catalog for dashboard surface")
    p.add_argument("--no-cn", action="store_true", help="skip A-share name index")
    args = p.parse_args()
    from kss.ui_surface.bind_catalog import build_catalog, save_catalog

    cat = build_catalog(include_cn=not args.no_cn)
    path = save_catalog(cat)
    print(
        f"bind_catalog: wrote {path} items={cat.get('item_count')} "
        f"domains={cat.get('domains_online')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
