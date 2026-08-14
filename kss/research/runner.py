"""Harness-backed execution for non-deterministic research nodes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from kss.llm.sanitizer import sanitize_llm_input

from .allowlist import bound_write_allowlist
from .harness_driver import ResearchHarnessDriver, ResearchTurnRequest

_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
_RESULT_KEYS = {
    "status",
    "claims",
    "evidence_refs",
    "artifact_refs",
    "open_questions",
    "warnings",
}


class AgentResearchTaskRunner:
    """将一个研究节点交给 ResearchHarnessDriver，由 overlay 判定完成。"""

    def __init__(
        self,
        *,
        state_root: Path,
        project_root: Path,
        driver: ResearchHarnessDriver | None = None,
        shared_agent: Any | None = None,
    ) -> None:
        del shared_agent
        self._state_root = Path(state_root)
        self._project_root = Path(project_root)
        self._driver = driver or ResearchHarnessDriver(
            state_root=self._state_root,
            project_root=self._project_root,
        )

    def abort(self, goal_id: str, reason: str = "research_paused") -> bool:
        return bool(self._driver.abort(goal_id, reason))

    def run(
        self,
        *,
        goal: dict[str, Any],
        task: dict[str, Any],
        attempt_id: str,
        dependency_summaries: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prompt = self._prompt(
            goal=goal,
            task=task,
            dependency_summaries=dependency_summaries,
        )
        origin = str(goal.get("origin") or "manual")
        request = self._driver.prepare_attempt(
            goal_id=str(goal.get("goal_id") or ""),
            task=task,
            attempt_id=attempt_id,
            origin=origin,
        )
        request.prompt = prompt
        turn = self._driver.run(request)
        extra = {
            "workspace": str(request.workspace),
            "agent_preset": request.agent_preset,
        }
        if turn.harness_status == "interrupted":
            return self._incomplete(
                turn.error or "interrupted",
                usage=dict(turn.usage),
                harness_status="interrupted",
                extra=extra,
            )
        if turn.harness_status not in {"completed", "succeeded"}:
            return self._incomplete(
                turn.error or turn.harness_status,
                usage=dict(turn.usage),
                harness_status=turn.harness_status,
                extra=extra,
            )
        parsed = self._parse_result(turn.assistant_text)
        combined = dict(turn.usage)
        if parsed is None:
            repair_req = ResearchTurnRequest(
                goal_id=request.goal_id,
                task=task,
                attempt_id=f"{attempt_id}-repair",
                prompt=(
                    "上一条回复不符合任务结果契约。请仅按指定六个字段输出合法 JSON，不调用工具。"
                ),
                origin=origin,
                workspace=request.workspace,
                allowlist=request.allowlist,
                agent_preset=request.agent_preset,
                attach_desktop_answerer=False,
                applied_write_ids=turn.applied_write_ids,
            )
            repair = self._driver.run(repair_req)
            combined = self._merge_usage(combined, dict(repair.usage))
            if repair.harness_status == "interrupted":
                return self._incomplete(
                    repair.error or "interrupted",
                    usage=combined,
                    harness_status="interrupted",
                    extra=extra,
                )
            parsed = self._parse_result(repair.assistant_text)
            turn = repair
        if parsed is None:
            return self._incomplete(
                "task_result_schema_invalid_after_repair",
                usage=combined,
                harness_status=turn.harness_status,
                extra=extra,
            )
        parsed["usage"] = combined
        parsed["_tool_evidence"] = list(turn.tool_evidence)
        parsed["_tool_results"] = list(turn.tool_results)
        parsed["harness_status"] = turn.harness_status
        parsed["workspace"] = str(request.workspace)
        parsed["write_allowlist"] = list(request.allowlist.tools)
        parsed["attach_desktop_answerer"] = False
        parsed["agent_preset"] = request.agent_preset
        return parsed

    def _prompt(
        self,
        *,
        goal: dict[str, Any],
        task: dict[str, Any],
        dependency_summaries: list[dict[str, Any]],
    ) -> str:
        writes = bound_write_allowlist(task)
        controlled = {
            "objective": self._sanitize_controlled(goal.get("objective")),
            "criterion": self._sanitize_controlled(task.get("title")),
            "snapshot": self._sanitize_controlled(goal.get("snapshot") or {}),
            "dependencies": self._sanitize_controlled(dependency_summaries),
            "allowed_tools": (task.get("payload") or {}).get("tool_whitelist") or [],
            "write_allowlist": writes,
            "agent": {
                "agent_id": task.get("agent_id"),
                "role": (task.get("payload") or {}).get("agent_role"),
                "instructions": self._sanitize_controlled(
                    (task.get("payload") or {}).get("agent_instructions")
                ),
                "can_submit_claims": bool(
                    (task.get("payload") or {}).get(
                        "can_submit_claims", True
                    )
                ),
                "can_verify_evidence": False,
            },
        }
        structured_hint = ""
        metric_by_kind = {
            "compute_temperature": (
                "m_temperature",
                "temperature_index",
            ),
            "theme_consensus": ("m_consensus", "theme_consensus"),
            "risk_radar": ("m_risk", "risk_radar"),
        }
        if task.get("kind") in metric_by_kind:
            metric_id, formula_id = metric_by_kind[str(task["kind"])]
            structured_hint = (
                " 本节点的 claims 至少一项应包含 metric 对象："
                f'{{"metric_id":"{metric_id}","value":数字,"unit":"%",'
                f'"formula_id":"{formula_id}",'
                '"formula_version":"v1","formula_inputs":[参与计算的原始数字],'
                '"input_refs":[对应成功工具的 tool_call_id、工具名或来源 URL],'
                '"as_of":"YYYY-MM-DD"}。value 必须等于 formula_inputs 的算术平均，'
                "不得填写未出现在对应工具结果中的数字。"
            )
        write_hint = (
            "写操作仅允许命中研究白名单且落在本 attempt 独立工作区；"
            "未命中直接拒绝且不问人。仓库根与数据库不可写。"
            if writes
            else "本节点写白名单为空，任何写都会失败关闭。"
        )
        return (
            "你正在执行 KSS 深度研究任务节点。"
            f"{write_hint}"
            "会话历史、长期记忆、Skill 文本和模型自身知识不能充当已验证证据。"
            "角色不能验证自己的 Evidence、修改 Criteria、提高预算或发布报告。"
            "模型文本不能标记研究目标完成；完成判定只属于研究 overlay 审计。"
            "只可引用本次成功工具结果中的来源。最终只输出一个 JSON 对象，不要 Markdown：\n"
            '{"status":"succeeded|incomplete","claims":[],"evidence_refs":[],'
            '"artifact_refs":[],"open_questions":[],"warnings":[]}\n'
            f"{structured_hint}"
            f"受控任务输入：{json.dumps(controlled, ensure_ascii=False, sort_keys=True)}"
        )

    def _parse_result(self, text: str) -> dict[str, Any] | None:
        match = _JSON_FENCE.match(text)
        candidate = match.group(1) if match else text
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, dict) or value.get("status") not in {
            "succeeded",
            "incomplete",
        }:
            return None
        if not _RESULT_KEYS.issubset(value):
            return None
        if any(
            not isinstance(value[key], list)
            for key in _RESULT_KEYS - {"status"}
        ):
            return None
        return {key: value[key] for key in _RESULT_KEYS}

    def _incomplete(
        self,
        warning: str,
        *,
        usage: dict[str, Any] | None = None,
        harness_status: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "status": "incomplete",
            "claims": [],
            "evidence_refs": [],
            "artifact_refs": [],
            "open_questions": [],
            "warnings": [warning],
            "usage": usage or {},
            "_tool_evidence": [],
            "_tool_results": [],
            "harness_status": harness_status or "incomplete",
            "attach_desktop_answerer": False,
        }
        payload.update(extra or {})
        return payload

    @staticmethod
    def _merge_usage(*items: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for usage in items:
            for key, value in usage.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    merged[key] = merged.get(key, 0) + value
                elif key not in merged:
                    merged[key] = value
        return merged

    @classmethod
    def _sanitize_controlled(cls, value: Any) -> Any:
        if isinstance(value, str):
            return sanitize_llm_input(value, max_len=2_000)
        if isinstance(value, list):
            return [cls._sanitize_controlled(item) for item in value[:50]]
        if isinstance(value, dict):
            return {
                str(key)[:128]: cls._sanitize_controlled(item)
                for key, item in list(value.items())[:100]
            }
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return sanitize_llm_input(str(value), max_len=2_000)
