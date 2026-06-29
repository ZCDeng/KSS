"""舆情热点 digest —— 从少而精的源捕捉集中热点方向与重大催化事件。

pipeline:采集(collect)→ 注入隔离(U3 sanitizer)→ 去重/真实性加权(dedup)→
跨独立源集中度热度榜(hotspot)→ LLM 受约束情绪 + 催化抽取 + 数字保护(commentary)
→ 题材匹配(theme_match)→ 关联标的(hotspot/bridge)→ 渲染归档(digest)。

数字一律由代码渲染,LLM 只产定性标签(沿用 kss.sector.commentary 三分模式)。
外部内容只作证据不作指令(R12,见 kss.research.evidence)。
"""
