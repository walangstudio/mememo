"""Phase C: curate_skills consolidates a drifted distilled-skill library."""

from __future__ import annotations

import numpy as np
import pytest

from mememo.context.skill_curator import cluster_duplicates, nearest


def test_cluster_duplicates_groups_similar_and_skips_singletons() -> None:
    # Two identical vectors + one orthogonal one: only the pair clusters.
    vectors = np.array([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    assert cluster_duplicates(vectors, threshold=0.86) == [[0, 1]]


def test_cluster_duplicates_chains_transitively() -> None:
    # A~B (0.95) and B~C (0.95) but A~C just under: union-find still groups all three.
    a = [1.0, 0.0]
    b = [0.95, np.sqrt(1 - 0.95**2)]
    c = [0.80, np.sqrt(1 - 0.80**2)]
    vectors = np.array([a, b, c], dtype=np.float32)
    # a·b ~= 0.95, b·c ~= 0.95, a·c ~= 0.80
    assert cluster_duplicates(vectors, threshold=0.9) == [[0, 1, 2]]


def test_cluster_duplicates_none_below_threshold() -> None:
    vectors = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    assert cluster_duplicates(vectors, threshold=0.86) == []


def test_nearest_returns_best_above_threshold() -> None:
    target = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    candidates = np.array([[0.0, 1.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float32)
    hit = nearest(target, candidates, threshold=0.9)
    assert hit is not None and hit[0] == 1 and hit[1] == pytest.approx(1.0)


def test_nearest_none_when_all_below() -> None:
    target = np.array([1.0, 0.0], dtype=np.float32)
    candidates = np.array([[0.0, 1.0]], dtype=np.float32)
    assert nearest(target, candidates, threshold=0.5) is None


def test_nearest_none_with_no_candidates() -> None:
    target = np.array([1.0, 0.0], dtype=np.float32)
    assert nearest(target, np.empty((0, 2), dtype=np.float32), threshold=0.0) is None


# --- integration with the real SkillStore + embedder -----------------------

pytest.importorskip("sentence_transformers")
pytest.importorskip("faiss")

from mememo.context.skill_store import SkillStore  # noqa: E402
from mememo.core.git_manager import GitManager  # noqa: E402
from mememo.core.memory_manager import MemoryManager  # noqa: E402
from mememo.core.storage_manager import StorageManager  # noqa: E402
from mememo.core.vector_index import VectorIndex  # noqa: E402
from mememo.embeddings.embedder import Embedder  # noqa: E402
from mememo.tools.curate_skills import curate_skills  # noqa: E402
from mememo.tools.manage_skill import SKILL_TAG_PREFIX, manage_skill  # noqa: E402
from mememo.tools.schemas import CurateSkillsParams, ManageSkillParams  # noqa: E402


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


async def _create(mm, ss, name, prompt, intent="coding"):
    await manage_skill(
        ManageSkillParams(action="create", name=name, intent=intent, prompt=prompt), ss, mm
    )


async def test_curate_reports_near_duplicate_cluster(mm, tmp_path) -> None:
    ss = SkillStore(base_dir=tmp_path)
    # Identical prompt → cosine 1.0 → a guaranteed near-duplicate cluster (dry run).
    await _create(mm, ss, "rebase-a", "rebase onto main, resolve conflicts, force-with-lease")
    await _create(mm, ss, "rebase-b", "rebase onto main, resolve conflicts, force-with-lease")

    res = await curate_skills(CurateSkillsParams(apply=False), ss, mm)
    assert res.success
    assert res.removed_exact == []  # dry run never deletes
    assert len(res.clusters) == 1
    names = {s["name"] for s in res.clusters[0]}
    assert names == {"rebase-a", "rebase-b"}
    assert res.passthrough and "Cluster 1" in res.passthrough_prompt


async def test_curate_apply_deletes_exact_duplicate(mm, tmp_path) -> None:
    ss = SkillStore(base_dir=tmp_path)
    await _create(mm, ss, "keep", "identical body", intent="coding")
    await _create(mm, ss, "drop", "identical body", intent="coding")
    # keep has the higher priority so it survives.
    ss.create_skill(name="keep", intent="coding", prompt="identical body", priority=5)

    res = await curate_skills(CurateSkillsParams(apply=True), ss, mm)
    assert res.success
    assert res.removed_exact == ["drop"]
    remaining = {s.name for s in ss.list_skills()}
    assert remaining == {"keep"}
    # The deleted skill's memory mirror is reaped too.
    assert not mm.storage_manager.get_memory_ids_by_tag(f"{SKILL_TAG_PREFIX}drop")


async def test_curate_distinct_skills_no_clusters(mm, tmp_path) -> None:
    ss = SkillStore(base_dir=tmp_path)
    await _create(mm, ss, "git-rebase", "rebase a git branch onto main and resolve conflicts")
    await _create(mm, ss, "pytest-async", "configure pytest-asyncio fixtures for an async suite")

    res = await curate_skills(CurateSkillsParams(apply=False), ss, mm)
    assert res.success
    assert res.clusters == []
    assert not res.passthrough


async def test_curate_noop_with_fewer_than_two_skills(mm, tmp_path) -> None:
    ss = SkillStore(base_dir=tmp_path)
    await _create(mm, ss, "only", "the one and only skill")
    res = await curate_skills(CurateSkillsParams(), ss, mm)
    assert res.success and res.clusters == [] and "nothing to curate" in res.message


async def test_create_nudges_when_near_duplicate_exists(mm, tmp_path) -> None:
    ss = SkillStore(base_dir=tmp_path)
    await _create(mm, ss, "first", "deploy by tagging a release and pushing the tag")
    resp = await manage_skill(
        ManageSkillParams(
            action="create",
            name="second",
            intent="coding",
            prompt="deploy by tagging a release and pushing the tag",
        ),
        ss,
        mm,
    )
    assert resp.success
    assert "similar to existing skill 'first'" in resp.message


async def test_create_no_nudge_when_unique(mm, tmp_path) -> None:
    ss = SkillStore(base_dir=tmp_path)
    await _create(mm, ss, "alpha", "rotate the database credentials quarterly")
    resp = await manage_skill(
        ManageSkillParams(
            action="create",
            name="beta",
            intent="testing",
            prompt="write a property-based test with hypothesis for the parser",
        ),
        ss,
        mm,
    )
    assert resp.success and "similar to existing skill" not in resp.message
