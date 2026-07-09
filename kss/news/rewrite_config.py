"""Constants for 资讯雷达 rewrite pipeline (plan 2026-07-10-001)."""

from __future__ import annotations

import os

# Max ready drafts produced by auto worker per track per BEIJING calendar day.
TOP_K: int = int(os.environ.get("KSS_REWRITE_TOP_K", "8"))

# Min ready drafts on a track/day before 今日要点 uses pool aggregate.
POOL_THRESHOLD: int = int(os.environ.get("KSS_REWRITE_POOL_THRESHOLD", "3"))

# Skip auto rewrite when title+summary+body chars below this floor.
THIN_CONTENT_CHARS: int = int(os.environ.get("KSS_REWRITE_THIN_CHARS", "40"))

# Soft wall-clock budget for one worker invocation (seconds).
WORKER_WALL_SEC: float = float(os.environ.get("KSS_REWRITE_WORKER_WALL_SEC", "600"))

# Max LLM calls per worker invocation (regardless of K × tracks).
WORKER_MAX_LLM_CALLS: int = int(os.environ.get("KSS_REWRITE_MAX_LLM", "96"))

# Stale generating claim reclaim after this many seconds.
GENERATING_TTL_SEC: float = float(os.environ.get("KSS_REWRITE_GENERATING_TTL", "900"))

# Aggregate digest bullet cap.
AGGREGATE_MAX_BULLETS: int = 8
