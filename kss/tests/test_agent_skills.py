from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from kss.agent import SkillManager
from kss.agent.skills import SkillResourceError


def _write_skill(root: Path, rel: str, text: str) -> Path:
    path = root / rel / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_skill_manager_discovers_roots_in_order_and_loads_capped_text(tmp_path):
    claude_root = tmp_path / ".claude" / "skills"
    agents_root = tmp_path / ".agents" / "skills"
    _write_skill(
        claude_root,
        "alpha",
        "---\nname: alpha\ndescription: A skill\n---\n" + "x" * 20_000,
    )
    _write_skill(agents_root, "nested/beta", "---\nname: beta\ndescription: B skill\n---\nbody")

    manager = SkillManager(tmp_path)
    skills, diagnostics = manager.discover()

    assert diagnostics == []
    assert [skill.name for skill in skills] == ["alpha", "beta"]
    alpha = skills[0]
    assert alpha.description == "A skill"
    assert len(manager.load_skill(alpha.id)) == 12_000
    assert len(manager.load_skill("alpha")) == 12_000


def test_skill_manager_parses_multiline_frontmatter_description(tmp_path):
    root = tmp_path / ".claude" / "skills"
    _write_skill(
        root,
        "alpha",
        "---\nname: alpha\ndescription: |\n  第一行说明\n  第二行说明\n---\nbody",
    )

    skill = SkillManager(tmp_path).discover()[0][0]

    assert skill.description == "第一行说明\n第二行说明"


def test_skill_manager_reports_duplicates_invalid_and_pin_limit(tmp_path):
    root = tmp_path / ".claude" / "skills"
    _write_skill(root, "alpha", "---\nname: dup\ndescription: one\n---\n")
    _write_skill(root, "other", "---\nname: dup\ndescription: two\n---\n")
    _write_skill(root, "bad", "---\nname: bad/name\ndescription: nope\n---\n")
    _write_skill(root, "ok1", "---\nname: ok1\n---\n")
    _write_skill(root, "ok2", "---\nname: ok2\n---\n")
    _write_skill(root, "ok3", "---\nname: ok3\n---\n")
    _write_skill(root, "ok4", "---\nname: ok4\n---\n")

    manager = SkillManager(tmp_path, state_root=tmp_path)
    skills, diagnostics = manager.discover()

    assert {item.code for item in diagnostics} == {"duplicate", "invalid"}
    ok_ids = [skill.id for skill in skills if skill.name.startswith("ok")]
    manager.pin_skill("s1", ok_ids[0])
    manager.pin_skill("s1", ok_ids[1])
    manager.pin_skill("s1", ok_ids[2])
    with pytest.raises(ValueError, match="最多加入 3 个"):
        manager.pin_skill("s1", ok_ids[3])

    manager.set_enabled(ok_ids[0], False)
    reloaded = SkillManager(tmp_path, state_root=tmp_path)
    skills_after_reload = {skill.id: skill for skill in reloaded.discover()[0]}
    assert skills_after_reload[ok_ids[0]].enabled is False


def test_skill_manager_status_and_resource_confinement(tmp_path):
    root = tmp_path / ".claude" / "skills"
    skill_path = _write_skill(root, "alpha", "---\nname: alpha\n---\nbody")
    (skill_path.parent / "ref.md").write_text("resource", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("nope", encoding="utf-8")

    manager = SkillManager(tmp_path)
    assert manager.status()["skills"] == 1
    assert manager.read_resource("alpha", "ref.md") == "resource"
    with pytest.raises(ValueError, match="技能目录"):
        manager.read_resource("alpha", "../../outside.md")


def test_skill_manager_rejects_large_skill(tmp_path):
    root = tmp_path / ".claude" / "skills"
    _write_skill(root, "huge", "---\nname: huge\n---\n" + "x" * (65 * 1024))

    manager = SkillManager(tmp_path)
    skill = manager.discover()[0][0]
    with pytest.raises(ValueError, match="超过 64KB"):
        manager.load_skill(skill.id)


def test_skill_resource_requires_enabled_skill(tmp_path):
    root = tmp_path / ".claude" / "skills"
    skill_path = _write_skill(root, "alpha", "---\nname: alpha\n---\nbody")
    (skill_path.parent / "ref.md").write_text("resource", encoding="utf-8")
    manager = SkillManager(tmp_path, state_root=tmp_path)
    skill = manager.discover()[0][0]
    manager.set_enabled(skill.id, False)

    with pytest.raises(SkillResourceError) as load_error:
        manager.load_skill(skill.id)
    with pytest.raises(SkillResourceError) as resource_error:
        manager.read_resource(skill.id, "ref.md")

    assert load_error.value.code == "skill_disabled"
    assert resource_error.value.code == "skill_disabled"


def test_skill_resource_pagination_reports_raw_size(tmp_path):
    root = tmp_path / ".claude" / "skills"
    skill_path = _write_skill(root, "alpha", "---\nname: alpha\n---\nbody")
    text = "页" * 20_000
    (skill_path.parent / "ref.md").write_text(text, encoding="utf-8")
    manager = SkillManager(tmp_path)

    first = manager.read_resource_info("alpha", "ref.md", limit=8_000)
    second = manager.read_resource_info(
        "alpha",
        "ref.md",
        offset=first.next_offset or 0,
        limit=8_000,
    )
    third = manager.read_resource_info(
        "alpha",
        "ref.md",
        offset=second.next_offset or 0,
        limit=8_000,
    )

    assert first.total_chars == 20_000
    assert first.byte_size == len(text.encode("utf-8"))
    assert first.truncated is True and first.next_offset == 8_000
    assert second.next_offset == 16_000
    assert third.next_offset is None and third.truncated is False
    assert first.content + second.content + third.content == text
    assert first.as_dict()["relative_path"] == "ref.md"


@pytest.mark.parametrize(
    ("relative_path", "expected_code"),
    [
        ("missing.md", "resource_not_found"),
        (".", "resource_not_file"),
        ("bad\x00name", "invalid_resource_path"),
        ("../../outside.md", "resource_path_escape"),
    ],
)
def test_skill_resource_stable_path_errors(tmp_path, relative_path, expected_code):
    root = tmp_path / ".claude" / "skills"
    _write_skill(root, "alpha", "---\nname: alpha\n---\nbody")
    manager = SkillManager(tmp_path)

    with pytest.raises(SkillResourceError) as error:
        manager.read_resource("alpha", relative_path)

    assert error.value.code == expected_code
    assert error.value.as_dict()["error"] == expected_code


def test_skill_resource_rejects_symlink_escape(tmp_path):
    root = tmp_path / ".claude" / "skills"
    skill_path = _write_skill(root, "alpha", "---\nname: alpha\n---\nbody")
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (skill_path.parent / "escape.md").symlink_to(outside)
    manager = SkillManager(tmp_path)

    with pytest.raises(SkillResourceError) as error:
        manager.read_resource("alpha", "escape.md")

    assert error.value.code == "resource_path_escape"


def test_skill_resource_rejects_binary_non_utf8_and_oversize(tmp_path):
    root = tmp_path / ".claude" / "skills"
    skill_path = _write_skill(root, "alpha", "---\nname: alpha\n---\nbody")
    (skill_path.parent / "nul.bin").write_bytes(b"text\x00binary")
    (skill_path.parent / "latin1.txt").write_bytes(b"caf\xe9")
    (skill_path.parent / "huge.txt").write_bytes(b"x" * (64 * 1024 + 1))
    manager = SkillManager(tmp_path)

    expected = {
        "nul.bin": "resource_binary",
        "latin1.txt": "resource_not_utf8",
        "huge.txt": "resource_too_large",
    }
    for path, code in expected.items():
        with pytest.raises(SkillResourceError) as error:
            manager.read_resource("alpha", path)
        assert error.value.code == code


def test_skill_script_is_returned_as_source_and_never_executed(tmp_path):
    root = tmp_path / ".claude" / "skills"
    skill_path = _write_skill(root, "alpha", "---\nname: alpha\n---\nbody")
    marker = tmp_path / "must-not-exist"
    script = skill_path.parent / "script.sh"
    source = f"#!/bin/sh\ntouch {marker}\n"
    script.write_text(source, encoding="utf-8")
    manager = SkillManager(tmp_path)

    assert manager.read_resource("alpha", "script.sh") == source
    assert not marker.exists()


def test_skill_manifest_exposes_provenance_and_required_tool_diagnostics(tmp_path):
    root = tmp_path / ".agents" / "skills"
    body = (
        "---\n"
        "name: evidence-review\n"
        "description: Evidence review\n"
        "category: analysis\n"
        "version: 2.1.0\n"
        "source: vibe-trading\n"
        "upstream_commit: abc123\n"
        "required_tools: [research_bundle, missing_tool]\n"
        "allowed_profiles:\n"
        "  - generic-research-v1\n"
        "protected: false\n"
        "---\n"
        "body"
    )
    path = _write_skill(root, "evidence-review", body)

    skill = SkillManager(
        tmp_path,
        available_tools={"research_bundle"},
    ).discover()[0][0]

    assert skill.category == "analysis"
    assert skill.version == "2.1.0"
    assert skill.source == "vibe-trading"
    assert skill.upstream_commit == "abc123"
    assert skill.content_hash == hashlib.sha256(path.read_bytes()).hexdigest()
    assert skill.trust == "packaged"
    assert skill.required_tools == ("research_bundle", "missing_tool")
    assert skill.allowed_profiles == ("generic-research-v1",)
    assert skill.available is False
    assert skill.missing_required_tools == ("missing_tool",)
    assert skill.enabled is False
    assert skill.as_dict()["missing_required_tools"] == ["missing_tool"]


def test_user_overlay_requires_approval_then_overrides_non_protected_skill(tmp_path):
    packaged_root = tmp_path / ".claude" / "skills"
    _write_skill(
        packaged_root,
        "alpha",
        "---\nname: alpha\ndescription: packaged\nsource: project\n---\npackaged",
    )
    user_root = tmp_path / "storage" / "agent" / "user_skills"
    _write_skill(
        user_root,
        "alpha",
        "---\nname: alpha\ndescription: user\nsource: forged-packaged\n---\nuser",
    )

    manager = SkillManager(tmp_path, state_root=tmp_path)
    skills, diagnostics = manager.discover()
    by_id = {skill.id: skill for skill in skills}
    user_id = next(skill.id for skill in skills if skill.path.is_relative_to(user_root))

    assert manager.load_skill("alpha").endswith("packaged")
    assert by_id[user_id].trust == "unreviewed"
    assert by_id[user_id].enabled is False
    assert any(item.code == "override_pending" for item in diagnostics)

    manager.set_trust(user_id, "user_approved")
    reloaded = SkillManager(tmp_path, state_root=tmp_path)
    approved = {skill.id: skill for skill in reloaded.discover()[0]}

    assert reloaded.load_skill("alpha").endswith("user")
    assert approved[user_id].active is True
    assert approved[user_id].trust == "user_approved"
    packaged = next(skill for skill in approved.values() if skill.id != user_id)
    assert packaged.active is False
    assert packaged.shadowed_by == user_id


def test_protected_packaged_skill_cannot_be_overridden_or_retrusted(tmp_path):
    packaged_root = tmp_path / ".claude" / "skills"
    packaged_path = _write_skill(
        packaged_root,
        "guard",
        (
            "---\nname: guard\ndescription: guard\nsource: kss-bundled\n"
            "protected: true\n---\nprotected"
        ),
    )
    user_root = tmp_path / "storage" / "agent" / "user_skills"
    _write_skill(
        user_root,
        "guard",
        "---\nname: guard\ndescription: override\n---\noverride",
    )

    manager = SkillManager(tmp_path, state_root=tmp_path)
    skills = manager.discover()[0]
    packaged = next(skill for skill in skills if skill.path == packaged_path.resolve())
    user = next(skill for skill in skills if skill.id.startswith("user-skills/"))
    manager.set_trust(user.id, "user_approved")

    after, diagnostics = manager.discover()
    active = next(skill for skill in after if skill.active)
    assert active.id == packaged.id
    assert manager.load_skill("guard").endswith("protected")
    assert any(item.code == "protected_override" for item in diagnostics)
    with pytest.raises(ValueError, match="只有用户"):
        manager.set_trust(packaged.id, "blocked")


def test_vibe_adapted_bundle_has_fixed_safe_skill_set_and_attribution():
    repo_root = Path(__file__).resolve().parents[2]
    adapted_root = repo_root / ".agents" / "skills" / "vibe-adapted"
    expected = {
        "research-discipline",
        "research-goal",
        "financial-statement",
        "macro-analysis",
        "corporate-events",
        "risk-analysis",
        "correlation-analysis",
        "sentiment-analysis",
        "report-generate",
        "thesis-review",
    }
    forbidden = {
        "买入",
        "卖出",
        "仓位",
        "目标价",
        "超配",
        "低配",
        "评级",
        " buy ",
        " sell ",
        " hold ",
        "target price",
        "overweight",
        "underweight",
    }

    skills = [
        skill
        for skill in SkillManager(repo_root).discover()[0]
        if skill.source == "vibe-trading"
    ]

    assert {skill.name for skill in skills} == expected
    assert all(skill.upstream_commit == "4cede84635df372e56ad4fb0a0647f19be56c892" for skill in skills)
    assert all(skill.trust == "packaged" and not skill.protected for skill in skills)
    for skill in skills:
        text = skill.path.read_text(encoding="utf-8").lower()
        assert not any(term in text for term in forbidden)
    notice = (adapted_root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "HKUDS/Vibe-Trading" in notice
    assert "MIT" in notice


def test_investment_analysis_skills_are_protected_method_only_entries() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    skills = {
        skill.name: skill
        for skill in SkillManager(repo_root).discover()[0]
        if skill.name
        in {"investment-daily-analysis", "investment-weekly-analysis"}
    }

    assert set(skills) == {
        "investment-daily-analysis",
        "investment-weekly-analysis",
    }
    assert all(skill.protected and skill.trust == "packaged" for skill in skills.values())
    assert skills["investment-daily-analysis"].allowed_profiles == (
        "investment-daily-v1",
    )
    assert skills["investment-weekly-analysis"].allowed_profiles == (
        "investment-weekly-v3",
    )
    forbidden_instructions = (
        "建议买入",
        "建议卖出",
        "目标价为",
        "建议仓位",
        "运行以下脚本",
        "自动运行脚本",
    )
    for skill in skills.values():
        text = skill.path.read_text(encoding="utf-8")
        assert not any(term in text for term in forbidden_instructions)


def test_cn_hk_equity_research_is_chat_bundled_and_not_weekly() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    manager = SkillManager(repo_root)
    manager.available_tools = frozenset({"resolve_listing", "run_equity_coverage"})
    skills, _ = manager.discover()
    skill = next(s for s in skills if s.name == "cn-hk-equity-research")
    assert skill.source == "kss-bundled"
    assert skill.protected is True
    assert skill.allowed_profiles == ("chat",)
    assert "investment-weekly-v3" not in skill.allowed_profiles
    assert "为什么动" in skill.description
    assert "研究" in skill.description
    assert skill.missing_required_tools == ()
    assert skill.enabled is True
    assert skill.available is True
    manager.available_tools = frozenset({"get_stock"})
    missing, _ = manager.discover()
    broken = next(s for s in missing if s.name == "cn-hk-equity-research")
    assert broken.missing_required_tools
    assert "run_equity_coverage" in broken.missing_required_tools or "resolve_listing" in broken.missing_required_tools
