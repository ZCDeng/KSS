from __future__ import annotations

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
    with pytest.raises(ValueError, match="最多置顶 3 个"):
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
