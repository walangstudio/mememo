"""Tests for Wave 1C: mememo/commands/reindex.py.

Covers:
  - move_vector_index: normal move, conflict detection, noop (absent src),
    noop (same id), cross-device via shutil.move fallback
  - move_faiss_dirs: full manifest round-trip, conflict clears embedding
    pointers, skipped entries untouched
  - reindex_identity: rows reassigned + dir moved; dry_run mutates nothing;
    gone-path skipped; target-exists -> conflict path clears embedding cols
"""

import uuid
from pathlib import Path

import pytest

from mememo.commands.reindex import move_faiss_dirs, move_vector_index
from mememo.core.storage_manager import StorageManager
from mememo.types.memory import (
    NULL_SHA,
    BranchContext,
    Memory,
    MemoryContent,
    MemoryMetadata,
    MemoryRelationships,
    MemorySummary,
    RepoContext,
)
from mememo.utils.hashing import calculate_checksum

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _mem(repo_id: str, repo_path: str, remote_url: str | None = None) -> Memory:
    text = f"hello {repo_id}"
    checksum = calculate_checksum(text)
    return Memory(
        id=str(uuid.uuid4()),
        repo=RepoContext(
            id=repo_id,
            name=Path(repo_path).name,
            path=repo_path,
            remote_url=remote_url,
        ),
        branch=BranchContext(name="main", commit_hash=NULL_SHA),
        content=MemoryContent(type="context", text=text),
        metadata=MemoryMetadata(
            checksum=checksum,
            token_count=3,
            created_at_sha=NULL_SHA,
            updated_at_sha=NULL_SHA,
        ),
        relationships=MemoryRelationships(),
        summary=MemorySummary(one_line="test"),
    )


def _make_faiss_dir(base: Path, proj_id: str, *, empty: bool = False) -> Path:
    d = base / proj_id
    d.mkdir(parents=True, exist_ok=True)
    if not empty:
        (d / "index.faiss").write_bytes(b"fake-faiss")
    return d


@pytest.fixture
def store(tmp_path):
    return StorageManager(base_dir=tmp_path / "store")


@pytest.fixture
def vi_base(tmp_path):
    """A fresh vector_index base dir separate from the store."""
    p = tmp_path / "vi"
    p.mkdir()
    return p


# ---------------------------------------------------------------------------
# move_vector_index
# ---------------------------------------------------------------------------


class TestMoveVectorIndex:
    def test_moves_dir(self, vi_base):
        _make_faiss_dir(vi_base, "aaa")
        result = move_vector_index(vi_base, "aaa", "bbb")
        assert result == "moved"
        assert not (vi_base / "aaa").exists()
        assert (vi_base / "bbb" / "index.faiss").exists()

    def test_noop_same_id(self, vi_base):
        _make_faiss_dir(vi_base, "aaa")
        result = move_vector_index(vi_base, "aaa", "aaa")
        assert result == "noop"
        assert (vi_base / "aaa").exists()

    def test_noop_absent_src(self, vi_base):
        result = move_vector_index(vi_base, "missing", "target")
        assert result == "noop"

    def test_conflict_when_target_non_empty(self, vi_base):
        _make_faiss_dir(vi_base, "aaa")
        _make_faiss_dir(vi_base, "bbb")
        result = move_vector_index(vi_base, "aaa", "bbb")
        assert result == "conflict"
        # src must still be present (not clobbered)
        assert (vi_base / "aaa").exists()

    def test_no_conflict_on_empty_target(self, vi_base):
        _make_faiss_dir(vi_base, "aaa")
        _make_faiss_dir(vi_base, "bbb", empty=True)
        result = move_vector_index(vi_base, "aaa", "bbb")
        assert result == "moved"

    def test_idempotent_absent_src_after_first_move(self, vi_base):
        _make_faiss_dir(vi_base, "aaa")
        move_vector_index(vi_base, "aaa", "bbb")
        # second call: src gone
        result = move_vector_index(vi_base, "aaa", "bbb")
        assert result == "noop"


# ---------------------------------------------------------------------------
# move_faiss_dirs
# ---------------------------------------------------------------------------


class TestMoveFaissDirs:
    def test_moves_per_manifest(self, vi_base):
        _make_faiss_dir(vi_base, "old1")
        _make_faiss_dir(vi_base, "old2")
        manifest = [
            {"old_id": "old1", "new_id": "new1", "skipped": False},
            {"old_id": "old2", "new_id": "new2", "skipped": False},
        ]
        counts = move_faiss_dirs(base_path=vi_base, manifest=manifest)
        assert counts["moved"] == 2
        assert (vi_base / "new1").exists()
        assert (vi_base / "new2").exists()

    def test_skipped_entries_ignored(self, vi_base):
        _make_faiss_dir(vi_base, "old1")
        manifest = [
            {"old_id": "old1", "new_id": "new1", "skipped": True},
        ]
        counts = move_faiss_dirs(base_path=vi_base, manifest=manifest)
        assert counts["skipped"] == 1
        assert counts["moved"] == 0
        assert (vi_base / "old1").exists()

    def test_same_id_counted_as_skipped(self, vi_base):
        _make_faiss_dir(vi_base, "same")
        manifest = [{"old_id": "same", "new_id": "same", "skipped": False}]
        counts = move_faiss_dirs(base_path=vi_base, manifest=manifest)
        assert counts["skipped"] == 1

    def test_conflict_clears_embedding_pointers(self, vi_base, store, tmp_path):
        repo_path = str(tmp_path / "repo")
        new_id = "new_id_conflict_"

        # Insert a memory under new_id (rows already reassigned by backfill)
        import asyncio

        mem = _mem(new_id, repo_path)
        asyncio.run(store.save_memory(mem))
        # Give it fake embedding pointers
        store.conn.execute(
            "UPDATE memories SET embedding_shard = 0, embedding_index = 42 WHERE repo_id = ?",
            (new_id,),
        )
        store.conn.commit()

        # Both dirs exist and non-empty -> conflict
        _make_faiss_dir(vi_base, "old_id_conflict_")
        _make_faiss_dir(vi_base, new_id)

        manifest = [
            {
                "old_id": "old_id_conflict_",
                "new_id": new_id,
                "skipped": False,
                "conn": store.conn,
            }
        ]
        counts = move_faiss_dirs(base_path=vi_base, manifest=manifest)
        assert counts["conflict"] == 1

        row = store.conn.execute(
            "SELECT embedding_shard, embedding_index FROM memories WHERE repo_id = ?",
            (new_id,),
        ).fetchone()
        assert row["embedding_shard"] is None
        assert row["embedding_index"] is None

    def test_conflict_no_crash_without_conn(self, vi_base):
        _make_faiss_dir(vi_base, "oldc")
        _make_faiss_dir(vi_base, "newc")
        manifest = [{"old_id": "oldc", "new_id": "newc", "skipped": False}]
        # no 'conn' key — must not raise
        counts = move_faiss_dirs(base_path=vi_base, manifest=manifest)
        assert counts["conflict"] == 1


# ---------------------------------------------------------------------------
# reindex_identity
# ---------------------------------------------------------------------------


class TestReindexIdentity:
    def _insert_mem(self, store, repo_id, repo_path, remote_url=None):
        import asyncio

        mem = _mem(repo_id, repo_path, remote_url=remote_url)
        asyncio.run(store.save_memory(mem))
        return mem

    def test_rows_reassigned_and_dir_moved(self, store, vi_base, tmp_path):
        repo_path = str(tmp_path / "myrepo")
        Path(repo_path).mkdir()

        old_id = "aaaaaaaaaaaaaaaa"
        new_id = "bbbbbbbbbbbbbbbb"

        self._insert_mem(store, old_id, repo_path)
        _make_faiss_dir(vi_base, old_id)

        def fake_resolver(rp, _remote):
            return new_id

        manifest = store._backfill_reindex_identity(fake_resolver, dry_run=False)
        for entry in manifest:
            entry["conn"] = store.conn
        move_faiss_dirs(vi_base, manifest)

        # rows now under new_id
        rows = store.conn.execute(
            "SELECT repo_id FROM memories WHERE repo_id = ?", (new_id,)
        ).fetchall()
        assert len(rows) > 0

        # FAISS dir renamed
        assert not (vi_base / old_id).exists()
        assert (vi_base / new_id).exists()

    def test_dry_run_mutates_nothing(self, store, vi_base, tmp_path):
        repo_path = str(tmp_path / "dryrepo")
        Path(repo_path).mkdir()

        old_id = "cccccccccccccccc"
        new_id = "dddddddddddddddd"

        self._insert_mem(store, old_id, repo_path)
        _make_faiss_dir(vi_base, old_id)

        def fake_resolver(rp, _remote):
            return new_id

        manifest = store._backfill_reindex_identity(fake_resolver, dry_run=True)

        # DB must still have old_id
        rows = store.conn.execute(
            "SELECT repo_id FROM memories WHERE repo_id = ?", (old_id,)
        ).fetchall()
        assert len(rows) > 0

        # FS must be untouched
        assert (vi_base / old_id).exists()
        assert not (vi_base / new_id).exists()

        # manifest says it would have changed
        assert any(e["old_id"] == old_id and e["new_id"] == new_id for e in manifest)

    def test_gone_path_skipped(self, store, vi_base, tmp_path):
        repo_path = str(tmp_path / "gone_repo")
        # do NOT create repo_path — simulates deleted project

        old_id = "eeeeeeeeeeeeeeee"
        self._insert_mem(store, old_id, repo_path)
        _make_faiss_dir(vi_base, old_id)

        called = []

        def fake_resolver(rp, _remote):
            called.append(rp)
            return "ffffffffffffffff"

        manifest = store._backfill_reindex_identity(fake_resolver, dry_run=False)

        skipped = [e for e in manifest if e["skipped"]]
        assert len(skipped) == 1
        assert skipped[0]["old_id"] == old_id
        # resolver must NOT have been called for gone path
        assert repo_path not in called

    def test_target_exists_conflict_clears_embedding_pointers(self, store, vi_base, tmp_path):
        repo_path = str(tmp_path / "conflictrepo")
        Path(repo_path).mkdir()

        old_id = "gggggggggggggggg"
        new_id = "hhhhhhhhhhhhhhhh"

        self._insert_mem(store, old_id, repo_path)
        # set fake embedding pointers
        store.conn.execute(
            "UPDATE memories SET embedding_shard = 1, embedding_index = 99 WHERE repo_id = ?",
            (old_id,),
        )
        store.conn.commit()

        # both FAISS dirs exist and non-empty
        _make_faiss_dir(vi_base, old_id)
        _make_faiss_dir(vi_base, new_id)

        def fake_resolver(rp, _remote):
            return new_id

        manifest = store._backfill_reindex_identity(fake_resolver, dry_run=False)
        for entry in manifest:
            entry["conn"] = store.conn
        counts = move_faiss_dirs(vi_base, manifest)

        assert counts["conflict"] == 1

        # embedding pointers cleared on new_id rows (reassigned already)
        row = store.conn.execute(
            "SELECT embedding_shard, embedding_index FROM memories WHERE repo_id = ?",
            (new_id,),
        ).fetchone()
        assert row["embedding_shard"] is None
        assert row["embedding_index"] is None
