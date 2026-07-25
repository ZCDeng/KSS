"""Agent Core 技能发现与装载."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kss.agent.jsonl import append_jsonl, read_jsonl_repair_tail, utc_timestamp


@dataclass(frozen=True)
class SkillInfo:
    """技能元信息.

    Args:
        id: 稳定技能 ID。
        name: 技能名称。
        description: 技能描述。
        path: SKILL.md 路径。
        enabled: 是否启用。
    """

    id: str
    name: str
    description: str
    path: Path
    enabled: bool = True


@dataclass(frozen=True)
class SkillDiagnostic:
    """技能诊断项."""

    code: str
    message: str
    path: Path | None = None


class SkillManager:
    """递归发现和安全装载本地技能."""

    MAX_PINS_PER_SESSION = 3
    MAX_SKILL_BYTES = 64 * 1024
    MAX_INJECT_CHARS = 12_000

    def __init__(self, repo_root: str | Path, state_root: str | Path | None = None) -> None:
        """初始化.

        Args:
            repo_root: 仓库根目录；只扫描 ``.claude/skills`` 和 ``.agents/skills``。
        """
        self.repo_root = Path(repo_root).resolve()
        self.roots = [self.repo_root / ".claude" / "skills", self.repo_root / ".agents" / "skills"]
        self.state_root = Path(state_root) if state_root is not None else self.repo_root
        self.state_path = self.state_root / "storage" / "agent" / "skills_state.jsonl"
        self._enabled: dict[str, bool] = {}
        self._pins: dict[str, tuple[str, ...]] = {}
        self._load_state()

    def discover(self) -> tuple[list[SkillInfo], list[SkillDiagnostic]]:
        """发现技能并返回诊断."""
        skills: list[SkillInfo] = []
        diagnostics: list[SkillDiagnostic] = []
        names: dict[str, Path] = {}
        for root in self.roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("SKILL.md")):
                resolved = path.resolve()
                if not self._is_relative_to(resolved, root.resolve()):
                    diagnostics.append(SkillDiagnostic("path_escape", "技能路径逃逸", path))
                    continue
                parsed = self._parse_frontmatter(resolved)
                name = parsed.get("name") or path.parent.name
                description = parsed.get("description", "")
                if not self._valid_name(name):
                    diagnostics.append(SkillDiagnostic("invalid", f"技能名称不合法: {name}", path))
                    continue
                if name in names:
                    diagnostics.append(SkillDiagnostic("duplicate", f"重复技能名称: {name}", path))
                    continue
                names[name] = path
                skill_id = self._skill_id(resolved)
                skills.append(
                    SkillInfo(
                        id=skill_id,
                        name=name,
                        description=description,
                        path=resolved,
                        enabled=self._enabled.get(skill_id, True),
                    )
                )
        return skills, diagnostics

    def set_enabled(self, skill_id: str, enabled: bool) -> None:
        """设置技能启用状态."""
        self._enabled[skill_id] = enabled
        self._append_state("enabled", {"skill_id": skill_id, "enabled": enabled})

    def pin_skill(self, session_id: str, skill_id: str) -> tuple[str, ...]:
        """为会话置顶技能，最多三个."""
        pins = list(self._pins.get(session_id, ()))
        if skill_id not in pins:
            if len(pins) >= self.MAX_PINS_PER_SESSION:
                raise ValueError("每个会话最多置顶 3 个技能")
            pins.append(skill_id)
        self._pins[session_id] = tuple(pins)
        self._append_state("pins", {"session_id": session_id, "pins": list(self._pins[session_id])})
        return self._pins[session_id]

    def unpin_skill(self, session_id: str, skill_id: str) -> tuple[str, ...]:
        """取消会话技能置顶."""
        pins = tuple(item for item in self._pins.get(session_id, ()) if item != skill_id)
        self._pins[session_id] = pins
        self._append_state("pins", {"session_id": session_id, "pins": list(pins)})
        return pins

    def pinned_skill_ids(self, session_id: str) -> tuple[str, ...]:
        """返回会话已置顶的技能 ID."""
        return self._pins.get(session_id, ())

    def load_skill(self, skill_id: str) -> str:
        """安全读取技能文本.

        Returns:
            最多 12k 字符的技能注入文本。
        """
        matches = [
            skill for skill in self.discover()[0] if skill.id == skill_id or skill.name == skill_id
        ]
        if not matches:
            raise KeyError(f"技能不存在: {skill_id}")
        path = matches[0].path.resolve()
        if not any(self._is_relative_to(path, root.resolve()) for root in self.roots if root.exists()):
            raise ValueError("技能路径不在允许目录内")
        data = path.read_bytes()
        if len(data) > self.MAX_SKILL_BYTES:
            raise ValueError("技能文件超过 64KB")
        return data.decode("utf-8", errors="replace")[: self.MAX_INJECT_CHARS]

    def status(self) -> dict[str, int]:
        """返回技能索引状态摘要."""
        skills, diagnostics = self.discover()
        enabled = sum(1 for skill in skills if skill.enabled)
        return {"skills": len(skills), "enabled": enabled, "diagnostics": len(diagnostics)}

    def read_resource(self, skill_id: str, relative_path: str) -> str:
        """读取技能目录内资源文本."""
        matches = [
            skill for skill in self.discover()[0] if skill.id == skill_id or skill.name == skill_id
        ]
        if not matches:
            raise KeyError(f"技能不存在: {skill_id}")
        base = matches[0].path.parent.resolve()
        path = (base / relative_path).resolve()
        if not self._is_relative_to(path, base):
            raise ValueError("资源路径不在技能目录内")
        data = path.read_bytes()
        if len(data) > self.MAX_SKILL_BYTES:
            raise ValueError("资源文件超过 64KB")
        return data.decode("utf-8", errors="replace")[: self.MAX_INJECT_CHARS]

    def _parse_frontmatter(self, path: Path) -> dict[str, str]:
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}
        parsed: dict[str, str] = {}
        index = 1
        while index < len(lines):
            line = lines[index]
            if line.strip() == "---":
                break
            if ":" not in line:
                index += 1
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value in {"|", ">"}:
                block: list[str] = []
                index += 1
                while index < len(lines):
                    block_line = lines[index]
                    if block_line.strip() == "---":
                        break
                    if block_line and not block_line[0].isspace():
                        break
                    block.append(block_line.strip())
                    index += 1
                separator = "\n" if value == "|" else " "
                parsed[key] = separator.join(block).strip()
                continue
            parsed[key] = value.strip("\"'")
            index += 1
        return parsed

    def _skill_id(self, path: Path) -> str:
        return path.relative_to(self.repo_root).as_posix()

    def _valid_name(self, name: str) -> bool:
        return bool(name) and "\n" not in name and "/" not in name and "\\" not in name

    def _is_relative_to(self, path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _append_state(self, event_type: str, payload: dict[str, Any]) -> None:
        append_jsonl(
            self.state_path,
            {
                "version": 1,
                "id": f"skill-{utc_timestamp()}",
                "parent_id": payload.get("skill_id") or payload.get("session_id"),
                "timestamp": utc_timestamp(),
                "type": event_type,
                "payload": payload,
            },
        )

    def _load_state(self) -> None:
        for entry in read_jsonl_repair_tail(self.state_path):
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            if entry.get("type") == "enabled":
                self._enabled[str(payload["skill_id"])] = bool(payload["enabled"])
            if entry.get("type") == "pins":
                self._pins[str(payload["session_id"])] = tuple(payload.get("pins", ()))
