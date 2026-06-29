#!/usr/bin/env python3
"""离线命中率评估器（U7 命中率 gate）.

拿一组近期真实舆情热点方向 + 人工标注的正确主题（ground truth），跑
:func:`kss.news.theme_match.match_theme`，产出三个数：

1. 召回率 recall      = (正确直达 + 正确降级) / 总数
2. 直达精度 precision = 正确直达 / 全部直达数
3. 错映射数 mis-map   = 直达但映射到与 ground truth 不同主题的条数

**脚本只产数字，不做放行判定**。R7 双门槛（召回 ≥70% 且直达精度 ≥90%）是
人工 gate（KTD6），本脚本永远 exit 0，不因未达标而非零退出。

跑法::

    /Users/zcdeng/projects/KSS/.venv/bin/python scripts/eval_theme_match.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kss.news.theme_match import match_theme  # noqa: E402

# 近期真实舆情热点方向 → 正确主题（None = 库内确实无对应域，正确做法是降级不直达）.
GROUND_TRUTH: list[tuple[str, str | None]] = [
    ("半导体", "半导体"),
    ("AI算力", "AI算力"),
    ("算力", "AI算力"),
    ("光模块", "AI算力"),
    ("CPO", "AI算力"),
    ("固态电池", "新能源储能"),
    ("储能", "新能源储能"),
    ("创新药", "生物医药"),
    ("具身智能", "具身智能"),
    ("低空经济", "工业母机·低空·航天"),
    ("黄金", "贵金属"),
    ("石油", "能源"),
    ("原油", "能源"),
    ("煤炭", "煤炭"),
    ("稀土", "有色金属"),
    ("降息", "降息受益"),
    ("稳定币", "稳定币数字货币"),
    ("数字货币", "稳定币数字货币"),
    ("房地产", "房地产"),
    ("地产", "房地产"),
    ("船舶", "船舶航运"),
    ("航运", "船舶航运"),
    ("白酒", "消费"),
    ("猪肉", "农业"),
    ("券商", "非银金融"),
    # 库内确实无对应域：正确行为是降级（不直达），用于检验「不臆造」.
    ("汇率", None),
    ("外资流入", None),
]


def main() -> int:
    total = len(GROUND_TRUTH)
    rows: list[dict] = []
    direct_hits = 0
    correct_direct = 0
    correct_degrade = 0
    mismaps = 0

    for hotword, truth in GROUND_TRUTH:
        res = match_theme(hotword)
        pred = res["theme"]
        is_direct = res["direct_hit"]

        if is_direct:
            direct_hits += 1
            if pred == truth and truth is not None:
                verdict = "✓直达"
                correct_direct += 1
            else:
                verdict = "✗错映射"
                mismaps += 1
        else:
            if truth is None:
                verdict = "✓降级"
                correct_degrade += 1
            else:
                verdict = "✗漏召回"

        rows.append({
            "hotword": hotword,
            "truth": truth if truth is not None else "(无/应降级)",
            "pred": pred if pred is not None else "(降级:%s)" % res["fallback"],
            "matched_on": res["matched_on"],
            "verdict": verdict,
        })

    recall = (correct_direct + correct_degrade) / total if total else 0.0
    precision = correct_direct / direct_hits if direct_hits else 1.0

    # —— 逐条表 ——
    print("=" * 78)
    print("逐条评估表（match_theme over ground truth）")
    print("=" * 78)
    header = f"{'热词':<12}{'ground truth':<18}{'预测':<18}{'匹配方式':<10}{'判定'}"
    print(header)
    print("-" * 78)
    for r in rows:
        print(
            f"{r['hotword']:<12}{r['truth']:<18}{r['pred']:<18}"
            f"{r['matched_on']:<10}{r['verdict']}"
        )
    print("-" * 78)

    # —— 三个数 ——
    print()
    print("=" * 78)
    print("命中率三数（人工 gate：召回 ≥70% 且 直达精度 ≥90% 才放行 U8）")
    print("=" * 78)
    print(f"总条数 total            = {total}")
    print(f"直达数 direct_hits      = {direct_hits}"
          f"（正确 {correct_direct} / 错映射 {mismaps}）")
    print(f"正确降级 correct_degrade = {correct_degrade}")
    print()
    print(f"(a) 召回率   recall    = {recall:.1%}  "
          f"= (正确直达 {correct_direct} + 正确降级 {correct_degrade}) / {total}")
    print(f"(b) 直达精度 precision  = {precision:.1%}  "
          f"= 正确直达 {correct_direct} / 直达 {direct_hits}")
    print(f"(c) 错映射数 mis-map    = {mismaps}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
