#!/usr/bin/env python3
"""Print Mach-O paths under a Harness tree. Used by sign_and_build.sh."""

from __future__ import annotations

import sys
from pathlib import Path

_MAGICS = {
    b"\xcf\xfa\xed\xfe",  # 64-bit LE
    b"\xce\xfa\xed\xfe",  # 32-bit LE
    b"\xca\xfe\xba\xbe",  # fat
    b"\xbe\xba\xfe\xca",
}


def list_macho(root: Path) -> list[str]:
    found: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            with path.open("rb") as handle:
                magic = handle.read(4)
        except OSError:
            continue
        if magic in _MAGICS:
            found.append(str(path))
    return sorted(found)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: list_harness_macho.py <harness-root>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    if not root.is_dir():
        print(f"ERROR: not a directory: {root}", file=sys.stderr)
        return 1
    for path in list_macho(root):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
