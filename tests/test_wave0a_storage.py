"""Tests for wave 0a storage additions: remote_url column and _backfill_reindex_identity."""

import uuid
from pathlib import Path

import pytest

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


def _make_memory(
    repo_id: str,
    repo_path: str,
    remote_url: str | None = None,
    branch: str = "main",
    content_text: str = "hello world",
) -> Memory:
    text = content_text
    checksum = calculate_checksum(text)
    return Memory(
        id=str(uuid.uuid4()),
        repo=RepoContext(
            id=repo_id,
            name=Path(repo_path).name,
            path=repo_path,
            remote_url=remote_url,
        ),
        branch=BranchContext(name=branch, commit_hash=NULL_SHA),
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


@pytest.fixture
def store(tmp_path):
    return StorageManager(base_dir=tmp_path / "store")


class TestRemoteUrlColumn:
    @pytest.mark.asyncio
    async def test_remote_url_persisted(self, store, tmp_path):
        mem = _make_memory(
            "abc1234567890123",
            str(tmp_path),
            remote_url="https://github.com/owner/repo.git",
        )
        await store.save_memory(mem)
        row = store.conn.execute(
            "SELECT remote_url FROM memories WHERE id = ?", (mem.id,)
        ).fetchone()
        assert row is not None
        assert row["remote_url"] == "https://github.com/owner/repo.git"

    @pytest.mark.asyncio
    async def test_remote_url_none_stored_as_null(self, store, tmp_path):
        mem = _make_memory("abc1234567890123", str(tmp_path), remote_url=None)
        await store.save_memory(mem)
        row = store.conn.execute(
            "SELECT remote_url FROM memories WHERE id = ?", (mem.id,)
        ).fetchone()
        assert row["remote_url"] is None

    @pytest.mark.asyncio
    async def test_remote_url_read_back_via_build_memory(self, store, tmp_path):
        remote = "git@github.com:owner/repo.git"
        mem = _make_memory("abc1234567890123", str(tmp_path), remote_url=remote)
        await store.save_memory(mem)
        row = dict(store.conn.execute("SELECT * FROM memories WHERE id = ?", (mem.id,)).fetchone())
        rebuilt = store._build_memory(row, [], [], [])
        assert rebuilt.repo.remote_url == remote


class TestBackfillReindexIdentity:
    @pytest.mark.asyncio
    async def test_dry_run_no_changes(self, store, tmp_path):
        mem = _make_memory("oldrepoident1234", str(tmp_path))
        await store.save_memory(mem)

        def resolver(path, remote_url):
            return "newrepoident1234"

        manifest = store._backfill_reindex_identity(resolver, dry_run=True)
        assert len(manifest) == 1
        assert manifest[0]["old_id"] == "oldrepoident1234"
        assert manifest[0]["new_id"] == "newrepoident1234"
        # dry_run: no actual DB change
        count = store.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE repo_id = 'oldrepoident1234'"
        ).fetchone()[0]
        assert count == 1

    @pytest.mark.asyncio
    async def test_live_run_renames_repo_id(self, store, tmp_path):
        mem = _make_memory("oldrepoident1234", str(tmp_path))
        await store.save_memory(mem)

        def resolver(path, remote_url):
            return "newrepoident1234"

        manifest = store._backfill_reindex_identity(resolver, dry_run=False)
        assert manifest[0]["new_id"] == "newrepoident1234"
        count_old = store.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE repo_id = 'oldrepoident1234'"
        ).fetchone()[0]
        count_new = store.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE repo_id = 'newrepoident1234'"
        ).fetchone()[0]
        assert count_old == 0
        assert count_new == 1

    @pytest.mark.asyncio
    async def test_no_change_when_ids_match(self, store, tmp_path):
        mem = _make_memory("sameid12345abcde", str(tmp_path))
        await store.save_memory(mem)

        def resolver(path, remote_url):
            return "sameid12345abcde"

        manifest = store._backfill_reindex_identity(resolver, dry_run=False)
        assert manifest[0]["row_count"] == 0
        assert manifest[0]["old_id"] == manifest[0]["new_id"]

    @pytest.mark.asyncio
    async def test_missing_path_skipped(self, store, tmp_path):
        # Use a path that doesn't exist on disk
        nonexistent = str(tmp_path / "nonexistent_repo")
        mem = _make_memory("oldrepoident5678", nonexistent)
        await store.save_memory(mem)

        calls = []

        def resolver(path, remote_url):
            calls.append(path)
            return "shouldneverrun1234"

        manifest = store._backfill_reindex_identity(resolver, dry_run=False)
        assert manifest[0]["skipped"] is True
        assert calls == []  # resolver never called for missing path

    @pytest.mark.asyncio
    async def test_resolver_exception_skipped(self, store, tmp_path):
        mem = _make_memory("oldrepoident9012", str(tmp_path))
        await store.save_memory(mem)

        def resolver(path, remote_url):
            raise RuntimeError("git exploded")

        manifest = store._backfill_reindex_identity(resolver, dry_run=False)
        assert manifest[0]["skipped"] is True
        # original row unchanged
        count = store.conn.execute(
            "SELECT COUNT(*) FROM memories WHERE repo_id = 'oldrepoident9012'"
        ).fetchone()[0]
        assert count == 1

    @pytest.mark.asyncio
    async def test_manifest_row_count_dry_run(self, store, tmp_path):
        # Two memories same repo
        mem1 = _make_memory("oldrepoident3456", str(tmp_path), content_text="memory one")
        mem2 = _make_memory("oldrepoident3456", str(tmp_path), content_text="memory two")
        await store.save_memory(mem1)
        await store.save_memory(mem2)

        def resolver(path, remote_url):
            return "newrepoident3456"

        manifest = store._backfill_reindex_identity(resolver, dry_run=True)
        # dry_run reports count from SELECT
        assert manifest[0]["row_count"] == 2


def test_connection_concurrency_pragmas(tmp_path):
    s = StorageManager(base_dir=tmp_path / "store")
    assert s.conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    assert s.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert s.conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
