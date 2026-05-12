"""Storage-layer tests for the v0.4 commit-aware vertical slice.

Covers Task IDs T001 (schema migration), T002 (Pydantic field additions),
T011 (idempotent v0.3 -> v0.4 backfill) and T012 (SHA stamping on the
write path). Includes the three QA-recommended hardening cases from the
magent-qa_engineer cross-check (2026-05-12): UPDATE re-stamps the SHA,
backfill does not duplicate blobs, and the memory_events CHECK accepts
all five documented ops.

These tests deliberately bypass MemoryManager so the suite runs without
sentence-transformers / torch.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import types as _types
from datetime import datetime
from pathlib import Path


# ---------- heavyweight-dep stubs (must run before any mememo import) -------

def _stub_module(name: str, **attrs: object) -> None:
    if name in sys.modules:
        return
    m = _types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m


class _StubSentenceTransformer:  # pragma: no cover — never instantiated here
    def __init__(self, *a, **k) -> None: ...


class _StubFaissIndex:  # pragma: no cover
    pass


_stub_module("sentence_transformers", SentenceTransformer=_StubSentenceTransformer)
_stub_module(
    "faiss",
    Index=_StubFaissIndex,
    IndexFlatL2=_StubFaissIndex,
    IndexIDMap=_StubFaissIndex,
    IndexIVFFlat=_StubFaissIndex,
)

import pytest  # noqa: E402

from mememo.core.storage_manager import StorageManager  # noqa: E402
from mememo.types import (  # noqa: E402
    BACKFILL_SHA,
    BranchContext,
    BranchState,
    GitContext,
    Memory,
    MemoryContent,
    MemoryEvent,
    MemoryMetadata,
    MemoryRelationships,
    MemorySummary,
    NULL_SHA,
    RepoContext,
)


# ---------- helpers ----------------------------------------------------------


SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
SHA_F = "f" * 40


def _make_storage(tmp_path: Path) -> "StorageManager":
    return StorageManager(base_dir=tmp_path / "store")


def _make_memory(
    sha: str = SHA_A,
    *,
    memory_id: str = "mem-1",
    branch: str = "main",
    content: str = "hello world",
    checksum_override: str | None = None,
) -> Memory:
    now = datetime.now()
    return Memory(
        id=memory_id,
        repo=RepoContext(id="repo-fixed", name="test-repo", path="/tmp/test-repo"),
        branch=BranchContext(name=branch, commit_hash=sha),
        content=MemoryContent(type="context", text=content),
        metadata=MemoryMetadata(
            checksum=checksum_override or f"chk-{memory_id}",
            token_count=2,
            created_at=now,
            updated_at=now,
            created_at_sha=sha,
            updated_at_sha=sha,
        ),
        relationships=MemoryRelationships(),
        summary=MemorySummary(one_line="hi"),
    )


def _save_and_emit_created(storage: "StorageManager", memory: Memory) -> None:
    """Mirrors MemoryManager.create_memory at the storage boundary."""
    asyncio.run(storage.save_memory(memory))
    storage.append_event(
        MemoryEvent(
            commit_sha=memory.branch.commit_hash,
            memory_id=memory.id,
            op="CREATED",
            content_sha=memory.metadata.checksum,
            branch=memory.branch.name,
            ts=memory.metadata.created_at,
        )
    )


# ---------- T001: schema migration ------------------------------------------


def test_t001_v04_columns_present(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)
    cols = {row[1] for row in storage.conn.execute("PRAGMA table_info(memories)")}
    assert {"created_at_sha", "updated_at_sha", "risk_grade"} <= cols


def test_t001_memory_events_op_check_rejects_unknown(tmp_path: Path) -> None:
    """The op CHECK constraint must reject unknown ops at the DB layer."""
    storage = _make_storage(tmp_path)
    storage.conn.execute(
        "INSERT INTO memory_events (commit_sha, memory_id, op, content_sha, branch, ts) "
        "VALUES (?, 'mem-1', 'CREATED', 'sha', 'main', 1)",
        (SHA_A,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        storage.conn.execute(
            "INSERT INTO memory_events (commit_sha, memory_id, op, content_sha, branch, ts) "
            "VALUES (?, 'mem-1', 'NOT_A_REAL_OP', 'sha', 'main', 1)",
            (SHA_A,),
        )


def test_t001_memory_events_sha_length_check_rejects_empty(tmp_path: Path) -> None:
    """Security hardening: CHECK(length(commit_sha)=40) must reject empty strings."""
    storage = _make_storage(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        storage.conn.execute(
            "INSERT INTO memory_events (commit_sha, memory_id, op, content_sha, branch, ts) "
            "VALUES ('', 'mem-1', 'CREATED', 'sha', 'main', 1)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        storage.conn.execute(
            "INSERT INTO memory_events (commit_sha, memory_id, op, content_sha, branch, ts) "
            "VALUES ('abc', 'mem-1', 'CREATED', 'sha', 'main', 1)"
        )


def test_t001_branch_state_table_upsert(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)
    storage.upsert_branch_state(
        BranchState(repo_id="r", branch="main", last_indexed_sha=SHA_A, parent_sha=None)
    )
    state = storage.get_branch_state("r", "main")
    assert state is not None
    assert state.last_indexed_sha == SHA_A

    storage.upsert_branch_state(
        BranchState(repo_id="r", branch="main", last_indexed_sha=SHA_B, parent_sha=SHA_C)
    )
    state2 = storage.get_branch_state("r", "main")
    assert state2 is not None
    assert state2.last_indexed_sha == SHA_B
    assert state2.parent_sha == SHA_C


# ---------- T002: Pydantic model fields -------------------------------------


def test_t002_memory_event_op_validation() -> None:
    ok = MemoryEvent(commit_sha=SHA_A, memory_id="m", op="CREATED", branch="main")
    assert ok.op == "CREATED"
    with pytest.raises(Exception):  # ValidationError
        MemoryEvent(commit_sha=SHA_A, memory_id="m", op="BOGUS", branch="main")  # type: ignore[arg-type]


def test_t002_memory_event_rejects_non_sha_commit() -> None:
    """Pydantic validator must reject anything that isn't 40-char hex."""
    for bad in ["", "abc", "z" * 40, "a" * 39, "a" * 41]:
        with pytest.raises(Exception):
            MemoryEvent(commit_sha=bad, memory_id="m", op="CREATED", branch="main")


def test_t002_memory_metadata_accepts_v04_fields() -> None:
    meta = MemoryMetadata(
        checksum="c",
        token_count=1,
        created_at_sha=SHA_A,
        updated_at_sha=SHA_A,
        risk_grade="WILL_BREAK",
    )
    assert meta.created_at_sha == SHA_A
    assert meta.risk_grade == "WILL_BREAK"


# ---------- T003: CHECK constraint accepts every documented op --------------


def test_t003_all_documented_ops_accepted_by_check(tmp_path: Path) -> None:
    """QA-recommended: completes the CHECK coverage that test_t001 only starts."""
    storage = _make_storage(tmp_path)
    for op in ("CREATED", "UPDATED", "STALED", "DELETED", "RESTORED"):
        storage.append_event(
            MemoryEvent(
                commit_sha=SHA_A, memory_id=f"m-{op}", op=op, branch="main"  # type: ignore[arg-type]
            )
        )
    rows = storage.conn.execute("SELECT op FROM memory_events ORDER BY id").fetchall()
    assert [r["op"] for r in rows] == ["CREATED", "UPDATED", "STALED", "DELETED", "RESTORED"]


# ---------- T011: idempotent backfill ---------------------------------------


def test_t011_backfill_seeds_synthetic_created_events_idempotent(tmp_path: Path) -> None:
    """A pre-v0.4 memory with a valid 40-char SHA gets exactly one CREATED event."""
    storage = _make_storage(tmp_path)
    storage.conn.execute(
        """
        INSERT INTO memories (
            id, repo_id, branch_name, commit_hash, content_type,
            checksum, content_ref, token_count, created_at, updated_at
        ) VALUES ('mem-pre', 'r', 'main', ?, 'context',
                  'sum', 'unused', 5, 1000, 1000)
        """,
        (SHA_A,),
    )
    storage.conn.commit()

    # Re-run backfill twice — the second call must be a no-op.
    storage._backfill_v04_commit_metadata()
    storage._backfill_v04_commit_metadata()

    events = storage.list_events(memory_id="mem-pre")
    assert len(events) == 1
    assert events[0].op == "CREATED"
    assert events[0].commit_sha == SHA_A
    assert events[0].content_sha == "sum"

    row = storage.conn.execute(
        "SELECT created_at_sha, updated_at_sha FROM memories WHERE id='mem-pre'"
    ).fetchone()
    assert row["created_at_sha"] == SHA_A
    assert row["updated_at_sha"] == SHA_A


def test_t011_backfill_uses_sentinel_for_legacy_rows_without_sha(tmp_path: Path) -> None:
    """Legacy rows with NULL / empty commit_hash get BACKFILL_SHA, not empty string."""
    storage = _make_storage(tmp_path)
    storage.conn.execute(
        """
        INSERT INTO memories (
            id, repo_id, branch_name, commit_hash, content_type,
            checksum, content_ref, token_count, created_at, updated_at
        ) VALUES ('mem-no-sha', 'r', 'main', NULL, 'context',
                  'sum', 'unused', 5, 1000, 1000)
        """
    )
    storage.conn.commit()
    storage._backfill_v04_commit_metadata()
    events = storage.list_events(memory_id="mem-no-sha")
    assert len(events) == 1
    assert events[0].commit_sha == BACKFILL_SHA


def test_t011_backfill_does_not_duplicate_blobs(tmp_path: Path) -> None:
    """QA-recommended: backfill must not write extra content blobs (FR-005)."""
    storage = _make_storage(tmp_path)
    blob_root = storage.content_dir
    storage.conn.execute(
        """
        INSERT INTO memories (
            id, repo_id, branch_name, commit_hash, content_type,
            checksum, content_ref, token_count, created_at, updated_at
        ) VALUES ('mem-pre-blob', 'r', 'main', ?, 'context',
                  'chk-pre', 'unused', 5, 1000, 1000)
        """,
        (SHA_A,),
    )
    storage.conn.commit()
    blobs_before = sorted(p.name for p in blob_root.rglob("*.json"))
    storage._backfill_v04_commit_metadata()
    blobs_after = sorted(p.name for p in blob_root.rglob("*.json"))
    assert blobs_before == blobs_after, "backfill must not write any content blobs"
    # And the synthetic event references the existing checksum, not a fresh blob.
    events = storage.list_events(memory_id="mem-pre-blob")
    assert events[0].content_sha == "chk-pre"


# ---------- T012: SHA persistence + update path -----------------------------


def test_t012_sha_persists_through_save_and_load(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)
    memory = _make_memory(sha=SHA_F, memory_id="mem-roundtrip")
    _save_and_emit_created(storage, memory)

    ctx = GitContext(repo=memory.repo, branch=memory.branch)
    reloaded = asyncio.run(storage.load_memory(memory.id, ctx))

    assert reloaded.metadata.created_at_sha == SHA_F
    assert reloaded.metadata.updated_at_sha == SHA_F


def test_t012_created_event_emitted_on_save(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)
    memory = _make_memory(sha=SHA_B, memory_id="mem-event")
    _save_and_emit_created(storage, memory)

    events = storage.list_events(memory_id=memory.id)
    assert [e.op for e in events] == ["CREATED"]
    assert events[0].commit_sha == SHA_B
    assert events[0].content_sha == memory.metadata.checksum
    assert events[0].branch == memory.branch.name


def test_t012_deleted_event_is_appendable(tmp_path: Path) -> None:
    storage = _make_storage(tmp_path)
    memory = _make_memory(sha=SHA_C, memory_id="mem-delete")
    _save_and_emit_created(storage, memory)

    storage.append_event(
        MemoryEvent(
            commit_sha=SHA_C,
            memory_id=memory.id,
            op="DELETED",
            content_sha=None,
            branch=memory.branch.name,
            ts=datetime.now(),
        )
    )
    ops = [e.op for e in storage.list_events(memory_id=memory.id)]
    assert ops == ["CREATED", "DELETED"]


def test_t012_update_path_restamps_sha_and_appends_updated_event(tmp_path: Path) -> None:
    """QA-recommended: prove FR-002 — updated_at_sha changes on re-save and
    a second UPDATED event is appended without disturbing created_at_sha."""
    storage = _make_storage(tmp_path)
    memory = _make_memory(sha=SHA_A, memory_id="mem-update")
    _save_and_emit_created(storage, memory)

    # Surgical update: rewrite updated_at_sha only.
    storage.update_memory_shas(memory.id, updated_at_sha=SHA_B)
    storage.append_event(
        MemoryEvent(
            commit_sha=SHA_B,
            memory_id=memory.id,
            op="UPDATED",
            content_sha=memory.metadata.checksum,
            branch=memory.branch.name,
        )
    )

    row = storage.conn.execute(
        "SELECT created_at_sha, updated_at_sha FROM memories WHERE id = ?", (memory.id,)
    ).fetchone()
    assert row["created_at_sha"] == SHA_A, "created_at_sha must NOT change on update"
    assert row["updated_at_sha"] == SHA_B
    ops = [e.op for e in storage.list_events(memory_id=memory.id)]
    assert ops == ["CREATED", "UPDATED"]


# ---------- Security hardening regression ------------------------------------


def test_security_null_sha_sentinel_is_valid_40_hex() -> None:
    assert len(NULL_SHA) == 40 and all(c in "0123456789abcdef" for c in NULL_SHA)
    assert len(BACKFILL_SHA) == 40 and all(c in "0123456789abcdef" for c in BACKFILL_SHA)
    assert NULL_SHA != BACKFILL_SHA
