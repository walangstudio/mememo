"""Tests for skill store."""

import shutil
import tempfile
from pathlib import Path

import pytest

from mememo.context.skill_store import Skill, SkillStore


@pytest.fixture
def store_dir():
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d)


@pytest.fixture
def store(store_dir):
    return SkillStore(base_dir=store_dir)


def test_empty_store(store):
    assert store.list_skills() == []
    assert store.get_skills_for_intent("coding", 1000) == []


def test_create_and_list(store):
    store.create_skill("test-skill", "debugging", "Debug workflow prompt", priority=5)
    skills = store.list_skills()
    assert len(skills) == 1
    assert skills[0].name == "test-skill"
    assert skills[0].intent == "debugging"
    assert skills[0].priority == 5
    assert skills[0].token_count > 0


def test_get_skill(store):
    store.create_skill("my-skill", "coding", "Code prompt here")
    skill = store.get_skill("my-skill")
    assert skill is not None
    assert skill.prompt == "Code prompt here"


def test_get_nonexistent(store):
    assert store.get_skill("nope") is None


def test_delete_skill(store):
    store.create_skill("to-delete", "general", "temp prompt")
    assert store.delete_skill("to-delete")
    assert store.get_skill("to-delete") is None
    assert not store.delete_skill("to-delete")


def test_intent_filtering(store):
    store.create_skill("debug1", "debugging", "Debug 1", priority=10)
    store.create_skill("code1", "coding", "Code 1", priority=5)
    store.create_skill("debug2", "debugging", "Debug 2", priority=5)

    debug_skills = store.get_skills_for_intent("debugging", 10000)
    assert len(debug_skills) == 2
    assert debug_skills[0].name == "debug1"  # higher priority first

    code_skills = store.get_skills_for_intent("coding", 10000)
    assert len(code_skills) == 1


def test_budget_enforcement(store):
    store.create_skill("big", "coding", "x " * 500, priority=10)
    store.create_skill("small", "coding", "tiny", priority=5)

    # Small budget should only fit the small skill
    skills = store.get_skills_for_intent("coding", 10)
    assert len(skills) <= 1
    if skills:
        assert skills[0].name == "small"


def test_yaml_persistence(store_dir):
    store1 = SkillStore(base_dir=store_dir)
    store1.create_skill("persistent", "testing", "Test prompt")

    # New store instance should pick up the file
    store2 = SkillStore(base_dir=store_dir)
    skills = store2.list_skills()
    assert len(skills) == 1
    assert skills[0].name == "persistent"


def test_skill_token_count():
    skill = Skill(name="test", intent="general", priority=0, prompt="hello world")
    assert skill.token_count > 0


def test_create_with_tags(store):
    store.create_skill("tagged", "review", "Review prompt", tags=["pr", "quality"])
    skill = store.get_skill("tagged")
    assert skill.tags == ["pr", "quality"]


def test_path_traversal_sanitized(store):
    skill = store.create_skill("../../etc/passwd", "general", "test prompt")
    # Name is sanitized to alphanumeric + hyphens/underscores
    assert "/" not in skill.name
    assert ".." not in skill.name
    assert skill.name == "etcpasswd"


def test_name_sanitization(store):
    store.create_skill("my skill!@#", "coding", "prompt here")
    skill = store.get_skill("myskill")
    assert skill is not None
