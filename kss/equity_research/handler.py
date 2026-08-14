"""对话工具入口：run_equity_coverage，支持 on_update 心跳。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from kss.equity_research.compile import compile_report, timestamped_stem
from kss.equity_research.intent import is_coverage_intent, r12_phrase
from kss.equity_research.spine import run_coverage

_CITE_QUERY = re.compile(r"仓位|Kelly|刚才的报告|标签|动作是|冻结")


def run_equity_coverage_tool(args: dict[str, Any], on_update=None) -> dict[str, Any]:
    query = str(args.get("query") or args.get("symbol") or "")
    mode = str(args.get("mode") or "full")
    fmt = str(args.get("format") or "pdf")
    assumptions = args.get("assumptions")
    if isinstance(assumptions, str) and assumptions.strip():
        assumptions = json.loads(assumptions)
    if not isinstance(assumptions, dict):
        assumptions = {}
    board = args.get("board") if isinstance(args.get("board"), dict) else None
    state = Path(args["output_dir"]) if args.get("output_dir") else _default_output()
    published = args.get("published") if isinstance(args.get("published"), dict) else None
    if published is None and _is_cite_query(query):
        published = _load_published(state)
    excerpts = args.get("excerpts") if isinstance(args.get("excerpts"), list) else None
    if published is None:
        excerpts = _maybe_research_excerpts(query, excerpts, on_update=on_update)
    result = run_coverage(
        query,
        mode,
        assumptions=assumptions,
        board=board,
        on_update=on_update,
        heartbeat_interval=float(args.get("heartbeat_interval") or 15),
        published=published,
        force_new=args.get("force_new"),
        history_years=args.get("history_years"),
        history_quarters=args.get("history_quarters"),
        vie_priced=args.get("vie_priced"),
        fundamentals=args.get("fundamentals") if isinstance(args.get("fundamentals"), dict) else None,
        excerpts=excerpts,
    )
    if result.get("r12"):
        return result
    if result.get("cited_only"):
        result["chat_summary"] = _cite_summary(result)
        return result
    r9 = result.get("r9")
    code = (result.get("listing") or {}).get("candidates") or [{}]
    code0 = code[0].get("code") if code else "unknown"
    markdown = str(args.get("markdown") or f"# {code0}\n\n覆盖正文只引用脚本字段。\n")
    markdown = _evidence_appendix(markdown, excerpts)
    compiled = compile_report(
        markdown,
        r9=r9,
        output_dir=state,
        stem=timestamped_stem(str(code0)),
        requested_format=fmt,
    )
    if not compiled.get("ok"):
        result["r12"] = compiled.get("r12") or r12_phrase("incomplete")
        result["status"] = "incomplete"
        result["artifacts"] = compiled
        return result
    result["artifacts"] = {
        "pdf": compiled.get("relative_pdf") or compiled.get("pdf"),
        "md": compiled.get("relative_md") or compiled.get("md"),
        "engine": compiled.get("engine"),
    }
    result["chat_summary"] = _chat_summary(result)
    _save_published(state, result)
    return result


def _maybe_research_excerpts(query: str, excerpts, on_update=None):
    if isinstance(excerpts, list) and excerpts:
        return excerpts
    try:
        from kss.research.adapter import research_bundle, research_status  # noqa: PLC0415
        status = research_status()
    except Exception:
        return excerpts
    if not status.get("available"):
        return excerpts
    if on_update:
        try:
            on_update({"message": "research_bundle", "step": "evidence"})
        except Exception:
            pass
    try:
        bundle = research_bundle(
            f"{query} 公告 年报 基本面 舆情",
            limit=3,
            max_chars_per_source=1200,
        )
    except Exception:
        return excerpts
    sources = [item for item in (bundle.get("sources") or []) if isinstance(item, dict)]
    return sources or excerpts


def _evidence_appendix(markdown: str, excerpts) -> str:
    if "外部背景（evidence-only）" in (markdown or ""):
        return markdown
    from kss.research.evidence import quarantine_rating_inputs  # noqa: PLC0415
    kept, _dropped = quarantine_rating_inputs(excerpts if isinstance(excerpts, list) else None)
    if not kept:
        return markdown
    lines = [
        markdown.rstrip(),
        "",
        "## 外部背景（evidence-only）",
        "",
        "以下摘录只作科目与背景，不得覆盖本地盘面，不得写成动作或仓位。",
        "",
    ]
    for item in kept[:5]:
        title = str(item.get("title") or "来源")
        url = str(item.get("url") or "")
        excerpt = str(item.get("excerpt") or item.get("summary") or "").strip()
        if len(excerpt) > 800:
            excerpt = excerpt[:800] + "…"
        lines.append(f"- {title}" + (f" ({url})" if url else ""))
        if excerpt:
            lines.append(f"  {excerpt}")
    return "\n".join(lines) + "\n"


def _is_cite_query(query: str) -> bool:
    raw = query or ""
    if is_coverage_intent(raw):
        return False
    return bool(_CITE_QUERY.search(raw))


def _published_path(output_dir: Path) -> Path:
    return output_dir / "last_published.json"


def _load_published(output_dir: Path) -> dict[str, Any] | None:
    path = _published_path(output_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _save_published(output_dir: Path, result: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "r9": result.get("r9"),
        "artifacts": result.get("artifacts"),
        "listing": result.get("listing"),
        "status": result.get("status"),
        "spine_ran": True,
    }
    _published_path(output_dir).write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _default_output() -> Path:
    import os
    root = Path(os.environ.get("KSS_STATE_ROOT") or ".")
    path = root / "storage" / "equity_research"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _chat_summary(result: dict[str, Any]) -> str:
    r9 = result.get("r9") or {}
    artifacts = result.get("artifacts") or {}
    lines = [
        f"覆盖完成。标签 {r9.get('label')}，动作 {r9.get('action')}，Kelly-lite {r9.get('kelly_lite')}。",
        f"PDF：{artifacts.get('pdf')}",
        f"Markdown 侧车：{artifacts.get('md')}",
    ]
    return "\n".join(lines)


def _cite_summary(result: dict[str, Any]) -> str:
    r9 = result.get("r9") or {}
    artifacts = result.get("artifacts") or {}
    return (
        f"引用已公布数字：标签 {r9.get('label')}，动作 {r9.get('action')}，"
        f"Kelly-lite {r9.get('kelly_lite')}。PDF：{artifacts.get('pdf')}"
    )
