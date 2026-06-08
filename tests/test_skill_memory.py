"""Phase B: distilled skills are a first-class, recallable memory type."""

from __future__ import annotations

from typing import get_args

import pytest

pytest.importorskip("sentence_transformers")
pytest.importorskip("faiss")

from mememo.context.intent_classifier import INTENT_TYPE_PRIORITIES  # noqa: E402
from mememo.context.skill_store import SkillStore  # noqa: E402
from mememo.core.git_manager import GitManager  # noqa: E402
from mememo.core.memory_manager import MemoryManager  # noqa: E402
from mememo.core.storage_manager import StorageManager  # noqa: E402
from mememo.core.vector_index import VectorIndex  # noqa: E402
from mememo.embeddings.embedder import Embedder  # noqa: E402
from mememo.tools.manage_skill import SKILL_TAG_PREFIX, manage_skill  # noqa: E402
from mememo.tools.recall_context import RECALL_TYPES  # noqa: E402
from mememo.tools.schemas import ManageSkillParams  # noqa: E402
from mememo.types.memory import (  # noqa: E402
    PERSISTENT_MEMORY_TYPES,
    CreateMemoryParams,
    MemoryContentType,
    MemoryRelationships,
    SearchParams,
)


def test_skill_is_first_class_type() -> None:
    assert "skill" in get_args(MemoryContentType)
    assert "skill" in PERSISTENT_MEMORY_TYPES
    assert "skill" in RECALL_TYPES
    # A relevant skill should be rankable for every intent.
    assert all("skill" in types for types in INTENT_TYPE_PRIORITIES.values())


@pytest.fixture
def mm(tmp_path):
    storage = StorageManager(base_dir=tmp_path)
    embedder = Embedder(model_name="minilm", device="cpu")
    vi = VectorIndex(
        base_path=tmp_path / "vector_index",
        repo_id="x",
        branch="main",
        dimension=embedder.dimension,
    )
    return MemoryManager(
        git_manager=GitManager(),
        storage_manager=storage,
        embedder=embedder,
        vector_index=vi,
        auto_sanitize=False,
        secrets_detection=False,
    )


async def _create_skill(mm, ss, name, intent, prompt):
    await manage_skill(
        ManageSkillParams(action="create", name=name, intent=intent, prompt=prompt), ss, mm
    )


async def test_create_mirrors_skill_into_recallable_memory(mm, tmp_path) -> None:
    ss = SkillStore(base_dir=tmp_path)
    await _create_skill(
        mm, ss, "safe-rebase", "coding", "rebase onto main, resolve, force-with-lease"
    )

    ids = mm.storage_manager.get_memory_ids_by_tag(f"{SKILL_TAG_PREFIX}safe-rebase")
    assert len(ids) == 1  # exactly one mirror memory

    res = await mm.recall_relevant(
        SearchParams(query="how to rebase a branch", top_k=5, min_similarity=0.0, hybrid=True),
        content_types={"skill"},
        include_global=True,
    )
    assert res, "the distilled skill must be semantically recallable"
    assert all(r.memory.content.type == "skill" for r in res)


async def test_create_is_upsert_no_duplicate_mirror(mm, tmp_path) -> None:
    ss = SkillStore(base_dir=tmp_path)
    await _create_skill(mm, ss, "dup", "coding", "first version")
    await _create_skill(mm, ss, "dup", "coding", "second version")
    ids = mm.storage_manager.get_memory_ids_by_tag(f"{SKILL_TAG_PREFIX}dup")
    assert len(ids) == 1  # re-create replaces, not duplicates


async def test_delete_removes_skill_mirror(mm, tmp_path) -> None:
    ss = SkillStore(base_dir=tmp_path)
    await _create_skill(mm, ss, "gone", "debugging", "bisect to find the bad commit")
    assert mm.storage_manager.get_memory_ids_by_tag(f"{SKILL_TAG_PREFIX}gone")

    await manage_skill(ManageSkillParams(action="delete", name="gone"), ss, mm)
    assert not mm.storage_manager.get_memory_ids_by_tag(f"{SKILL_TAG_PREFIX}gone")


async def test_skill_delete_spares_lookalike_user_memory(mm, tmp_path) -> None:
    # A non-skill user memory that happens to carry the reserved tag must NOT be
    # touched by a skill delete (scoped by content_type='skill' + GLOBAL lane).
    ss = SkillStore(base_dir=tmp_path)
    user_mem = await mm.create_memory(
        CreateMemoryParams(
            content="just a note",
            type="context",
            tags=[f"{SKILL_TAG_PREFIX}victim"],
            relationships=MemoryRelationships(),
        ),
        force_global=True,
    )
    await _create_skill(mm, ss, "victim", "coding", "the real reusable skill")
    await manage_skill(ManageSkillParams(action="delete", name="victim"), ss, mm)

    remaining = mm.storage_manager.get_memory_ids_by_tag(f"{SKILL_TAG_PREFIX}victim")
    assert remaining == [user_mem.id]  # user memory survives; only the skill mirror was removed


async def test_delete_reaps_mirror_for_unsanitized_name(mm, tmp_path) -> None:
    # The YAML store sanitizes names (e.g. "git ops" -> "gitops"); the mirror is
    # tagged with the sanitized name. Deleting with the raw name must still match.
    ss = SkillStore(base_dir=tmp_path)
    await manage_skill(
        ManageSkillParams(action="create", name="git ops", intent="coding", prompt="how to"), ss, mm
    )
    safe = SkillStore.sanitize_name("git ops")
    assert mm.storage_manager.get_memory_ids_by_tag(f"{SKILL_TAG_PREFIX}{safe}")

    await manage_skill(ManageSkillParams(action="delete", name="git ops"), ss, mm)
    assert not mm.storage_manager.get_memory_ids_by_tag(f"{SKILL_TAG_PREFIX}{safe}")


async def test_manage_skill_without_memory_manager_is_noop_mirror(mm, tmp_path) -> None:
    # Back-compat: callers that don't pass a memory_manager still work (no mirror).
    ss = SkillStore(base_dir=tmp_path)
    await manage_skill(
        ManageSkillParams(action="create", name="nomir", intent="coding", prompt="x"), ss
    )
    assert ss.get_skill("nomir") is not None
    assert not mm.storage_manager.get_memory_ids_by_tag(f"{SKILL_TAG_PREFIX}nomir")
