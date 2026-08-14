"""对话工具入口：run_equity_coverage，支持 on_update 心跳。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kss.equity_research.compile import compile_report, timestamped_stem
from kss.equity_research.intent import r12_phrase
from kss.equity_research.spine import run_coverage


def run_equity_coverage_tool(args: dict[str, Any], on_update=None) -> dict[str, Any]:
    query = str(args.get("query") or args.get("symbol") or "")
    mode = str(args.get("mode") or "full")
    fmt = str(args.get("format") or "pdf")
    assumptions = args.get("assumptions")
    if isinstance(assumptions, str) and assumptions.strip():
        import json
        assumptions = json.loads(assumptions)
    if not isinstance(assumptions, dict):
        assumptions = {}
    board = args.get("board") if isinstance(args.get("board"), dict) else None
    result = run_coverage(
        query,
        mode,
        assumptions=assumptions,
        board=board,
        on_update=on_update,
        heartbeat_interval=float(args.get("heartbeat_interval") or 15),
        published=args.get("published") if isinstance(args.get("published"), dict) else None,
        force_new=args.get("force_new"),
        history_years=args.get("history_years"),
        history_quarters=args.get("history_quarters"),
        vie_priced=args.get("vie_priced"),
        fundamentals=args.get("fundamentals") if isinstance(args.get("fundamentals"), dict) else None,
        excerpts=args.get("excerpts") if isinstance(args.get("excerpts"), list) else None,
    )
    if result.get("r12"):
        return result
    r9 = result.get("r9")
    code = (result.get("listing") or {}).get("candidates") or [{}]
    code0 = code[0].get("code") if code else "unknown"
    state = Path(args["output_dir"]) if args.get("output_dir") else _default_output()
    compiled = compile_report(
        str(args.get("markdown") or f"# {code0}\n\n覆盖正文只引用脚本字段。\n"),
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
    return result


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
