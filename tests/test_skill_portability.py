"""Phase D: skills export/import as agentskills.io SKILL.md (portable round-trip)."""

from __future__ import annotations

import pytest

from mememo.context.skill_portability import parse_skillmd, skill_to_skillmd, skillmd_name
from mememo.context.skill_store import Skill


def test_skillmd_name_coerces_to_spec() -> None:
    assert skillmd_name("Git Ops") == "git-ops"
    assert skillmd_name("deploy_release") == "deploy-release"
    assert skillmd_name("--Weird__Name!!--") == "weird-name"
    assert skillmd_name("") == "skill"
    assert skillmd_name("x" * 80) == "x" * 64


def test_skill_to_skillmd_has_required_frontmatter() -> None:
    skill = Skill(name="safe-rebase", intent="coding", priority=3, prompt="line one\nline two")
    name, text = skill_to_skillmd(skill)
    assert name == "safe-rebase"
    assert text.startswith("---\n")
    assert "name: safe-rebase" in text
    assert "description: line one" in text  # first prompt line becomes the description
    assert text.rstrip().endswith("line two")


def test_roundtrip_preserves_fields() -> None:
    skill = Skill(
        name="deploy-flow",
        intent="review",
        priority=7,
        prompt="step 1: tag\nstep 2: push\nedge case: dirty tree",
        tags=["ci", "release"],
    )
    _, text = skill_to_skillmd(skill)
    data = parse_skillmd(text)
    assert data == {
        "name": "deploy-flow",
        "intent": "review",
        "priority": 7,
        "prompt": "step 1: tag\nstep 2: push\nedge case: dirty tree",
        "tags": ["ci", "release"],
    }


def test_name_normalizes_on_roundtrip() -> None:
    skill = Skill(name="My_Cool_Skill", intent="coding", priority=0, prompt="do the thing")
    _, text = skill_to_skillmd(skill)
    assert parse_skillmd(text)["name"] == "my-cool-skill"


def test_parse_foreign_skillmd_without_metadata() -> None:
    # A SKILL.md authored elsewhere (no mememo metadata) imports as a general skill.
    text = (
        "---\n"
        "name: foreign-skill\n"
        "description: something useful\n"
        "---\n\n"
        "the body is the prompt\n"
    )
    data = parse_skillmd(text)
    assert data == {
        "name": "foreign-skill",
        "intent": "general",
        "priority": 0,
        "prompt": "the body is the prompt",
        "tags": [],
    }


def test_parse_returns_none_without_name_or_body() -> None:
    assert parse_skillmd("no frontmatter, just text") is None
    assert parse_skillmd("---\nname: x\n---\n\n   ") is None  # empty body
    assert parse_skillmd("---\ndescription: d\n---\n\nbody") is None  # no name


def test_parse_rejects_non_scalar_name() -> None:
    # A YAML list/dict name would str() into a garbled identifier — reject it.
    assert parse_skillmd("---\nname:\n- a\n- b\ndescription: d\n---\n\nbody\n") is None


def test_parse_canonicalizes_foreign_name() -> None:
    # A foreign 'git_ops' converges with our 'git-ops' so round-trips don't multiply.
    text = "---\nname: git_ops\ndescription: d\n---\n\nbody\n"
    assert parse_skillmd(text)["name"] == "git-ops"


def test_parse_tolerates_bad_priority_and_tags() -> None:
    text = (
        "---\n"
        "name: messy\n"
        "description: d\n"
        "metadata:\n"
        "  intent: testing\n"
        "  priority: not-a-number\n"
        "  tags: oops-not-a-list\n"
        "---\n\n"
        "body\n"
    )
    data = parse_skillmd(text)
    assert data["priority"] == 0 and data["tags"] == [] and data["intent"] == "testing"


@pytest.mark.parametrize("subcmd", ["export-skills", "import-skills"])
def test_subcommands_registered(subcmd) -> None:
    from mememo.__main__ import _SUBCOMMANDS

    assert subcmd in _SUBCOMMANDS


def test_export_writes_skillmd_tree(tmp_path, monkeypatch) -> None:
    # End-to-end export: a populated SkillStore -> <out>/<name>/SKILL.md, re-parsable.
    from mememo.__main__ import _cmd_export_skills
    from mememo.context.skill_store import SkillStore

    store_dir = tmp_path / "store"
    ss = SkillStore(base_dir=store_dir)
    ss.create_skill("alpha", "coding", "rebase onto main", priority=2, tags=["git"])
    ss.create_skill("beta", "testing", "write a pytest fixture")

    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(store_dir))
    out = tmp_path / "out"
    rc = _cmd_export_skills([str(out)])
    assert rc == 0

    alpha = (out / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    data = parse_skillmd(alpha)
    assert data["name"] == "alpha" and data["intent"] == "coding" and data["tags"] == ["git"]
    assert (out / "beta" / "SKILL.md").is_file()


def test_export_skips_name_collisions(tmp_path, monkeypatch) -> None:
    # Two distinct skill names that collapse to one SKILL.md dir must not clobber.
    from mememo.__main__ import _cmd_export_skills
    from mememo.context.skill_store import SkillStore

    store_dir = tmp_path / "store"
    ss = SkillStore(base_dir=store_dir)
    ss.create_skill("git-ops", "coding", "hyphen variant")
    ss.create_skill("git_ops", "coding", "underscore variant")

    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(store_dir))
    out = tmp_path / "out"
    assert _cmd_export_skills([str(out)]) == 0
    dirs = sorted(p.name for p in out.iterdir() if p.is_dir())
    assert dirs == ["git-ops"]  # collision skipped, only one dir written
