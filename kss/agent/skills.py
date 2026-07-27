"""Agent Core 技能发现与装载."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kss.agent.jsonl import append_jsonl, read_jsonl_repair_tail, utc_timestamp

_TRUST_VALUES = frozenset({"packaged", "user_approved", "unreviewed", "blocked"})


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
    category: str = "general"
    version: str = "unversioned"
    source: str = "project"
    upstream_commit: str | None = None
    content_hash: str = ""
    trust: str = "packaged"
    required_tools: tuple[str, ...] = ()
    allowed_profiles: tuple[str, ...] = ()
    protected: bool = False
    active: bool = True
    shadowed_by: str | None = None
    available: bool = True
    missing_required_tools: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """转换为 protocol/UI 可选字段，不改变旧字段语义."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "category": self.category,
            "version": self.version,
            "source": self.source,
            "upstream_commit": self.upstream_commit,
            "content_hash": self.content_hash,
            "trust": self.trust,
            "required_tools": list(self.required_tools),
            "allowed_profiles": list(self.allowed_profiles),
            "protected": self.protected,
            "active": self.active,
            "shadowed_by": self.shadowed_by,
            "available": self.available,
            "missing_required_tools": list(self.missing_required_tools),
        }


@dataclass(frozen=True)
class SkillDiagnostic:
    """技能诊断项."""

    code: str
    message: str
    path: Path | None = None


@dataclass(frozen=True)
class SkillResource:
    """已验证的技能文本资源及分页信息."""

    skill_id: str
    skill_name: str
    relative_path: str
    content: str
    byte_size: int
    total_chars: int
    offset: int
    next_offset: int | None
    truncated: bool

    def as_dict(self) -> dict[str, Any]:
        """转换为稳定 tool/protocol payload."""
        return {
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "relative_path": self.relative_path,
            "content": self.content,
            "byte_size": self.byte_size,
            "total_chars": self.total_chars,
            "offset": self.offset,
            "next_offset": self.next_offset,
            "truncated": self.truncated,
        }


class SkillResourceError(ValueError):
    """具有稳定错误码的 Skill 读取失败."""

    def __init__(self, code: str, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.path = path

    def as_dict(self) -> dict[str, Any]:
        """转换为模型可修正的结构化错误."""
        result: dict[str, Any] = {"error": self.code, "message": str(self)}
        if self.path is not None:
            result["path"] = self.path
        return result


@dataclass(frozen=True)
class _SkillRoot:
    path: Path
    kind: str
    order: int


@dataclass(frozen=True)
class _SkillCandidate:
    info: SkillInfo
    root: _SkillRoot
    layer: int


class SkillManager:
    """递归发现和安全装载本地技能."""

    MAX_PINS_PER_SESSION = 3
    MAX_SKILL_BYTES = 64 * 1024
    MAX_INJECT_CHARS = 12_000

    def __init__(
        self,
        repo_root: str | Path,
        state_root: str | Path | None = None,
        *,
        available_tools: Iterable[str] | None = None,
    ) -> None:
        """初始化.

        Args:
            repo_root: 仓库根目录；只扫描 ``.claude/skills`` 和 ``.agents/skills``。
        """
        self.repo_root = Path(repo_root).resolve()
        self.state_root = (
            Path(state_root).resolve() if state_root is not None else self.repo_root
        )
        self.roots = [
            self.repo_root / ".claude" / "skills",
            self.repo_root / ".agents" / "skills",
        ]
        self.user_root = self.state_root / "storage" / "agent" / "user_skills"
        self._roots = (
            _SkillRoot(self.roots[0], "packaged", 0),
            _SkillRoot(self.roots[1], "packaged", 1),
            _SkillRoot(self.user_root, "user", 2),
        )
        self.state_path = self.state_root / "storage" / "agent" / "skills_state.jsonl"
        self.available_tools = (
            frozenset(str(item) for item in available_tools)
            if available_tools is not None
            else None
        )
        self._enabled: dict[str, bool] = {}
        self._pins: dict[str, tuple[str, ...]] = {}
        self._trust: dict[str, str] = {}
        self._load_state()

    def discover(self) -> tuple[list[SkillInfo], list[SkillDiagnostic]]:
        """发现技能并返回诊断."""
        candidates: list[_SkillCandidate] = []
        diagnostics: list[SkillDiagnostic] = []
        seen_in_layer: dict[tuple[int, str], Path] = {}
        for root in self._roots:
            if not root.path.exists():
                continue
            resolved_root = root.path.resolve()
            for path in sorted(root.path.rglob("SKILL.md")):
                resolved = path.resolve()
                if not self._is_relative_to(resolved, resolved_root):
                    diagnostics.append(SkillDiagnostic("path_escape", "技能路径逃逸", path))
                    continue
                try:
                    parsed = self._parse_frontmatter(resolved)
                except OSError as exc:
                    diagnostics.append(
                        SkillDiagnostic("unreadable", f"技能无法读取: {exc}", path)
                    )
                    continue
                name = parsed.get("name") or path.parent.name
                description = parsed.get("description", "")
                if not isinstance(name, str) or not self._valid_name(name):
                    diagnostics.append(SkillDiagnostic("invalid", f"技能名称不合法: {name}", path))
                    continue
                source_default = "user" if root.kind == "user" else "project"
                source = self._metadata_text(parsed.get("source"), source_default)
                protected = (
                    root.kind == "packaged"
                    and self._metadata_bool(parsed.get("protected"), False)
                )
                layer = self._source_layer(root, source, protected)
                layer_key = (layer, name)
                if layer_key in seen_in_layer:
                    diagnostics.append(SkillDiagnostic("duplicate", f"重复技能名称: {name}", path))
                    continue
                seen_in_layer[layer_key] = path
                if not isinstance(description, str):
                    diagnostics.append(
                        SkillDiagnostic("invalid_manifest", "description 必须是文本", path)
                    )
                    continue
                skill_id = self._skill_id(resolved, root)
                trust = self._candidate_trust(skill_id, root)
                required_tools = self._string_list(parsed.get("required_tools"))
                missing_tools = (
                    tuple(
                        tool
                        for tool in required_tools
                        if tool not in self.available_tools
                    )
                    if self.available_tools is not None
                    else ()
                )
                candidates.append(
                    _SkillCandidate(
                        info=SkillInfo(
                            id=skill_id,
                            name=name,
                            description=description,
                            path=resolved,
                            enabled=False,
                            category=self._metadata_text(
                                parsed.get("category"), "general"
                            ),
                            version=self._metadata_text(
                                parsed.get("version"), "unversioned"
                            ),
                            source=source,
                            upstream_commit=self._optional_text(
                                parsed.get("upstream_commit")
                            ),
                            content_hash=self._content_hash(resolved),
                            trust=trust,
                            required_tools=required_tools,
                            allowed_profiles=self._string_list(
                                parsed.get("allowed_profiles")
                            ),
                            protected=protected,
                            available=not missing_tools,
                            missing_required_tools=missing_tools,
                        ),
                        root=root,
                        layer=layer,
                    )
                )
        candidates.sort(key=lambda item: (item.layer, item.root.order, str(item.info.path)))
        skills = self._resolve_precedence(candidates, diagnostics)
        return skills, diagnostics

    def set_enabled(self, skill_id: str, enabled: bool) -> None:
        """设置技能启用状态."""
        self._enabled[skill_id] = enabled
        self._append_state("enabled", {"skill_id": skill_id, "enabled": enabled})

    def set_trust(self, skill_id: str, trust: str) -> None:
        """审批或阻断用户 Skill；打包 Skill 的 packaged 身份不可伪造."""
        if trust not in _TRUST_VALUES - {"packaged"}:
            raise ValueError("trust 必须是 user_approved、unreviewed 或 blocked")
        skill = self._resolve_skill(skill_id, require_enabled=False)
        if not self._is_relative_to(skill.path.resolve(), self.user_root.resolve()):
            raise ValueError("只有用户 overlay Skill 可以修改信任状态")
        self._trust[skill.id] = trust
        self._append_state("trust", {"skill_id": skill.id, "trust": trust})

    def pin_skill(self, session_id: str, skill_id: str) -> tuple[str, ...]:
        """将技能加入会话上下文，最多三个。"""
        pins = list(self._pins.get(session_id, ()))
        if skill_id not in pins:
            if len(pins) >= self.MAX_PINS_PER_SESSION:
                raise ValueError("每个会话最多加入 3 个技能")
            pins.append(skill_id)
        self._pins[session_id] = tuple(pins)
        self._append_state("pins", {"session_id": session_id, "pins": list(self._pins[session_id])})
        return self._pins[session_id]

    def unpin_skill(self, session_id: str, skill_id: str) -> tuple[str, ...]:
        """将技能移出会话上下文。"""
        pins = tuple(item for item in self._pins.get(session_id, ()) if item != skill_id)
        self._pins[session_id] = pins
        self._append_state("pins", {"session_id": session_id, "pins": list(pins)})
        return pins

    def pinned_skill_ids(self, session_id: str) -> tuple[str, ...]:
        """返回当前会话已选择的技能 ID（协议字段保留原名）。"""
        return self._pins.get(session_id, ())

    def load_skill(self, skill_id: str) -> str:
        """安全读取技能文本.

        Returns:
            最多 12k 字符的技能注入文本。
        """
        skill = self._resolve_skill(skill_id, require_enabled=True)
        path = skill.path.resolve()
        if not any(
            self._is_relative_to(path, root.path.resolve())
            for root in self._roots
            if root.path.exists()
        ):
            raise SkillResourceError("skill_path_escape", "技能路径不在允许目录内")
        text, _byte_size = self._read_text_file(path, label="技能文件")
        return text[: self.MAX_INJECT_CHARS]

    def status(self) -> dict[str, int]:
        """返回技能索引状态摘要."""
        skills, diagnostics = self.discover()
        enabled = sum(1 for skill in skills if skill.enabled)
        return {"skills": len(skills), "enabled": enabled, "diagnostics": len(diagnostics)}

    def read_resource(
        self,
        skill_id: str,
        relative_path: str,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> str:
        """读取技能目录内的一页文本资源.

        脚本文件只作为源码文本返回；本方法从不导入、启动或执行资源。
        需要 ``next_offset`` 等分页信息的调用方使用 :meth:`read_resource_info`。
        """
        return self.read_resource_info(
            skill_id,
            relative_path,
            offset=offset,
            limit=limit,
        ).content

    def read_resource_info(
        self,
        skill_id: str,
        relative_path: str,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> SkillResource:
        """验证并读取资源，返回原始大小和稳定分页元数据."""
        skill = self._resolve_skill(skill_id, require_enabled=True)
        if not isinstance(relative_path, str) or not relative_path or "\x00" in relative_path:
            raise SkillResourceError(
                "invalid_resource_path",
                "资源路径不能为空或包含 NUL",
                path=str(relative_path),
            )
        if offset < 0:
            raise SkillResourceError("invalid_offset", "offset 必须大于等于 0")
        page_limit = self.MAX_INJECT_CHARS if limit is None else limit
        if page_limit <= 0 or page_limit > self.MAX_INJECT_CHARS:
            raise SkillResourceError(
                "invalid_limit",
                f"limit 必须位于 1..{self.MAX_INJECT_CHARS}",
            )

        base = skill.path.parent.resolve()
        try:
            path = (base / relative_path).resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise SkillResourceError(
                "invalid_resource_path",
                f"资源路径不合法: {relative_path}",
                path=relative_path,
            ) from exc
        if not self._is_relative_to(path, base):
            raise SkillResourceError(
                "resource_path_escape",
                "资源路径不在技能目录内",
                path=relative_path,
            )
        text, byte_size = self._read_text_file(path, label="资源文件")
        total_chars = len(text)
        start = min(offset, total_chars)
        end = min(start + page_limit, total_chars)
        next_offset = end if end < total_chars else None
        return SkillResource(
            skill_id=skill.id,
            skill_name=skill.name,
            relative_path=path.relative_to(base).as_posix(),
            content=text[start:end],
            byte_size=byte_size,
            total_chars=total_chars,
            offset=start,
            next_offset=next_offset,
            truncated=next_offset is not None,
        )

    def _resolve_skill(self, skill_id: str, *, require_enabled: bool) -> SkillInfo:
        discovered = self.discover()[0]
        exact = [skill for skill in discovered if skill.id == skill_id]
        matches = exact or [
            skill for skill in discovered if skill.name == skill_id and skill.active
        ]
        if not matches:
            raise SkillResourceError("skill_not_found", f"技能不存在: {skill_id}")
        skill = matches[0]
        if require_enabled and not skill.enabled:
            raise SkillResourceError("skill_disabled", f"技能未启用: {skill.name}")
        return skill

    def _read_text_file(self, path: Path, *, label: str) -> tuple[str, int]:
        try:
            if not path.exists():
                raise SkillResourceError(
                    "resource_not_found",
                    f"{label}不存在",
                    path=str(path),
                )
            if not path.is_file():
                raise SkillResourceError(
                    "resource_not_file",
                    f"{label}不是普通文件",
                    path=str(path),
                )
            byte_size = path.stat().st_size
            if byte_size > self.MAX_SKILL_BYTES:
                raise SkillResourceError(
                    "resource_too_large",
                    f"{label}超过 64KB",
                    path=str(path),
                )
            data = path.read_bytes()
        except SkillResourceError:
            raise
        except OSError as exc:
            raise SkillResourceError(
                "resource_unreadable",
                f"{label}无法读取: {exc}",
                path=str(path),
            ) from exc
        if b"\x00" in data or self._looks_binary(data):
            raise SkillResourceError(
                "resource_binary",
                f"{label}不是文本文件",
                path=str(path),
            )
        try:
            return data.decode("utf-8"), len(data)
        except UnicodeDecodeError as exc:
            raise SkillResourceError(
                "resource_not_utf8",
                f"{label}不是 UTF-8 文本",
                path=str(path),
            ) from exc

    def _looks_binary(self, data: bytes) -> bool:
        if not data:
            return False
        text_controls = {9, 10, 12, 13}
        suspicious = sum(
            1 for byte in data if byte < 32 and byte not in text_controls
        )
        return suspicious / len(data) > 0.05

    def _parse_frontmatter(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return {}
        parsed: dict[str, Any] = {}
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
            if not value:
                items: list[str] = []
                lookahead = index + 1
                while lookahead < len(lines):
                    item_line = lines[lookahead]
                    if item_line.strip() == "---":
                        break
                    stripped = item_line.strip()
                    if not stripped.startswith("- "):
                        break
                    items.append(stripped[2:].strip().strip("\"'"))
                    lookahead += 1
                if items:
                    parsed[key] = items
                    index = lookahead
                    continue
            parsed[key] = self._parse_scalar(value)
            index += 1
        return parsed

    def _skill_id(self, path: Path, root: _SkillRoot) -> str:
        if root.kind == "user":
            return f"user-skills/{path.relative_to(root.path.resolve()).as_posix()}"
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
            if entry.get("type") == "trust":
                trust = str(payload.get("trust") or "")
                if trust in _TRUST_VALUES - {"packaged"}:
                    self._trust[str(payload["skill_id"])] = trust

    def _resolve_precedence(
        self,
        candidates: list[_SkillCandidate],
        diagnostics: list[SkillDiagnostic],
    ) -> list[SkillInfo]:
        by_name: dict[str, list[_SkillCandidate]] = {}
        name_order: list[str] = []
        for candidate in candidates:
            if candidate.info.name not in by_name:
                name_order.append(candidate.info.name)
            by_name.setdefault(candidate.info.name, []).append(candidate)

        resolved: list[SkillInfo] = []
        for name in name_order:
            group = by_name[name]
            packaged = [item for item in group if item.root.kind == "packaged"]
            users = [item for item in group if item.root.kind == "user"]
            protected = next((item for item in packaged if item.info.protected), None)
            approved = next(
                (item for item in users if item.info.trust == "user_approved"),
                None,
            )
            winner = protected or approved or (packaged[0] if packaged else None)
            if protected and users:
                for item in users:
                    diagnostics.append(
                        SkillDiagnostic(
                            "protected_override",
                            f"受保护技能不能被用户 overlay 覆盖: {name}",
                            item.info.path,
                        )
                    )
            elif users and not approved and packaged:
                for item in users:
                    diagnostics.append(
                        SkillDiagnostic(
                            "override_pending",
                            f"用户 overlay 尚未批准，不会覆盖: {name}",
                            item.info.path,
                        )
                    )

            winner_id = winner.info.id if winner is not None else None
            for candidate in group:
                info = candidate.info
                active = winner_id == info.id
                trusted = info.trust in {"packaged", "user_approved"}
                enabled = (
                    active
                    and trusted
                    and info.available
                    and self._enabled.get(info.id, True)
                )
                resolved.append(
                    SkillInfo(
                        **{
                            **info.__dict__,
                            "enabled": enabled,
                            "active": active,
                            "shadowed_by": None if active else winner_id,
                        }
                    )
                )
        return resolved

    def _candidate_trust(self, skill_id: str, root: _SkillRoot) -> str:
        if root.kind == "packaged":
            return "packaged"
        return self._trust.get(skill_id, "unreviewed")

    def _source_layer(self, root: _SkillRoot, source: str, protected: bool) -> int:
        if root.kind == "user":
            return 3
        if protected and source == "kss-bundled":
            return 0
        if source == "vibe-trading":
            return 1
        return 2

    def _parse_scalar(self, value: str) -> Any:
        clean = value.strip()
        if clean.startswith("[") and clean.endswith("]"):
            inner = clean[1:-1].strip()
            if not inner:
                return []
            return [item.strip().strip("\"'") for item in inner.split(",")]
        if clean.lower() in {"true", "false"}:
            return clean.lower() == "true"
        return clean.strip("\"'")

    def _string_list(self, value: Any) -> tuple[str, ...]:
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, list):
            return tuple(
                str(item).strip()
                for item in value
                if isinstance(item, str) and item.strip()
            )
        return ()

    def _metadata_text(self, value: Any, default: str) -> str:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return default

    def _optional_text(self, value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _metadata_bool(self, value: Any, default: bool) -> bool:
        return value if isinstance(value, bool) else default

    def _content_hash(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
