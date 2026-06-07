"""Regression: VectorIndex.add must persist the faiss shard to disk.

Pre-fix, add() only wrote a shard on shard-full (50k) or 5-min eviction, so a
short-lived process exited with vectors in memory only — mappings.db kept the
rows but the shard file was never written and the next process searched an
empty index. These tests instantiate a SECOND VectorIndex on the same dir to
simulate a fresh process.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("faiss")

from mememo.core.vector_index import VectorIndex  # noqa: E402


def _vec(seed: int, dim: int = 8) -> list[float]:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(dim).astype("float32").tolist()


def test_add_writes_shard_to_disk(tmp_path):
    vi = VectorIndex(base_path=tmp_path, repo_id="r", branch="main", dimension=8)
    vi.add([_vec(1)], ["m1"], ["c1"])
    shard = tmp_path / "r" / "main" / "shard_0.faiss"
    assert shard.exists(), "add() must write the faiss shard, not only mappings.db"


def test_search_survives_fresh_instance(tmp_path):
    # Process 1: add.
    vi1 = VectorIndex(base_path=tmp_path, repo_id="r", branch="main", dimension=8)
    q = _vec(42)
    vi1.add([q, _vec(7)], ["target", "other"], ["c1", "c2"])

    # Process 2: a brand-new instance (cold loaded_shards) must find it.
    vi2 = VectorIndex(base_path=tmp_path, repo_id="r", branch="main", dimension=8)
    distances, memory_ids = vi2.search(q, top_k=2)
    assert "target" in memory_ids, "vectors added by a prior process must be searchable"


def test_dimension_mismatch_raises_actionable_error(tmp_path):
    # Build an index at dim 8 (simulating the old embedding model).
    vi1 = VectorIndex(base_path=tmp_path, repo_id="r", branch="main", dimension=8)
    vi1.add([_vec(1, dim=8)], ["m1"], ["c1"])

    # A new model with a different dimension (e.g. minilm 384 -> qwen3 1024) must
    # fail with a clear re-index message, not an opaque FAISS assertion.
    vi2 = VectorIndex(base_path=tmp_path, repo_id="r", branch="main", dimension=16)
    with pytest.raises(ValueError, match="dimension mismatch"):
        vi2.search(_vec(2, dim=16), top_k=1)


def test_incremental_adds_all_searchable_from_fresh_instance(tmp_path):
    vi1 = VectorIndex(base_path=tmp_path, repo_id="r", branch="main", dimension=8)
    ids = []
    for i in range(5):
        mid = f"m{i}"
        ids.append(mid)
        vi1.add([_vec(i)], [mid], [f"c{i}"])  # one-at-a-time, like create_memory

    vi2 = VectorIndex(base_path=tmp_path, repo_id="r", branch="main", dimension=8)
    for i, mid in enumerate(ids):
        _, found = vi2.search(_vec(i), top_k=1)
        assert found and found[0] == mid, f"{mid} not searchable from a fresh instance"
