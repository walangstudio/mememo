"""v0.6 batch 1 — worktree canonical root + migrate-worktrees + MCP resources."""

from __future__ import annotations

import json
import sys
import types as _types
from pathlib import Path


def _stub_module(name: str, **attrs: object) -> None:
    if name in sys.modules:
        return
    m = _types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m


class _Stub:  # pragma: no cover
    def __init__(self, *a, **k) -> None: ...

    def tool(self, *a, **k):
        def deco(fn):
            return fn
        return deco

    def resource(self, *a, **k):
        def deco(fn):
            return fn
        return deco


_stub_module("sentence_transformers", SentenceTransformer=_Stub)
_stub_module(
    "faiss", Index=_Stub, IndexFlatL2=_Stub, IndexIDMap=_Stub, IndexIVFFlat=_Stub
)
_stub_module("fastmcp", FastMCP=_Stub)


import pytest  # noqa: E402

from mememo import resources  # noqa: E402
from mememo.core.storage_manager import StorageManager  # noqa: E402
from mememo.types import BranchState, Relation  # noqa: E402


SHA = "a" * 40


# ---------- T029: canonical_repo_root --------------------------------------


def test_t029_canonical_repo_root_parses_common_dir(tmp_path: Path) -> None:
    """canonical_repo_root strips trailing `.git` when git emits it."""
    import asyncio

    from mememo.core.git_manager import GitManager

    gm = GitManager()

    async def fake_exec(command, args, cwd=None):
        # Simulate `git rev-parse --git-common-dir` returning the absolute
        # path to .git/ — the canonical worktree-merge case.
        if command == "rev-parse" and args == ["--git-common-dir"]:
            return str(tmp_path / ".git")
        raise RuntimeError("not stubbed")

    gm._exec_git = fake_exec  # type: ignore[assignment]
    result = asyncio.run(gm.canonical_repo_root(cwd=str(tmp_path)))
    assert result == str(tmp_path)


def test_t029_canonical_repo_root_falls_back_when_git_unavailable(
    tmp_path: Path,
) -> None:
    """When git rev-parse fails, fall back to find_repo_root."""
    import asyncio

    from mememo.core.git_manager import GitManager

    gm = GitManager()
    called = {"find": 0}

    async def boom(*a, **k):
        raise RuntimeError("git not available")

    async def find(*a, **k):
        called["find"] += 1
        return "/repo/root"

    gm._exec_git = boom  # type: ignore[assignment]
    gm.find_repo_root = find  # type: ignore[assignment]
    result = asyncio.run(gm.canonical_repo_root(cwd=str(tmp_path)))
    assert result == "/repo/root"
    assert called["find"] == 1


# ---------- T030: reassign_repo_id ------------------------------------------


def test_t030_reassign_repo_id_rewrites_every_table(tmp_path: Path) -> None:
    storage = StorageManager(base_dir=tmp_path / "store")
    # Seed a memory + relation + branch_state under the OLD repo_id.
    storage.conn.execute(
        "INSERT INTO memories (id, repo_id, branch_name, content_type, "
        "  checksum, content_ref, token_count, created_at, updated_at) "
        "VALUES ('m1', 'old', 'main', 'context', 'k', 'u', 1, 1, 1)"
    )
    storage.upsert_branch_state(
        BranchState(repo_id="old", branch="main", last_indexed_sha=SHA)
    )
    storage.insert_relations([
        Relation(
            id="r1", repo_id="old", branch="main",
            source_memory_id="m1", target_memory_id="m1",
            type="CALLS", confidence="EXTRACTED", created_at_sha=SHA,
        )
    ])
    storage.conn.commit()

    counts = storage.reassign_repo_id("old", "new")
    assert counts.get("memories", 0) == 1
    assert counts.get("branch_state", 0) == 1
    assert counts.get("relations", 0) == 1

    # Old rows are gone; new rows present.
    assert storage.conn.execute(
        "SELECT COUNT(*) AS n FROM memories WHERE repo_id = 'old'"
    ).fetchone()["n"] == 0
    assert storage.conn.execute(
        "SELECT COUNT(*) AS n FROM memories WHERE repo_id = 'new'"
    ).fetchone()["n"] == 1


def test_t030_reassign_repo_id_noop_on_self_target(tmp_path: Path) -> None:
    storage = StorageManager(base_dir=tmp_path / "store")
    counts = storage.reassign_repo_id("same", "same")
    assert counts == {}


# ---------- T031: MCP resources --------------------------------------------


class _StubMM:
    def __init__(self, storage: StorageManager) -> None:
        self.storage_manager = storage


def _seed(storage: StorageManager) -> None:
    # 2 memories, one stale; 2 relations in community 0; one branch_state.
    storage.conn.execute(
        "INSERT INTO memories (id, repo_id, branch_name, content_type, "
        "  file_path, function_name, class_name, "
        "  checksum, content_ref, token_count, created_at, updated_at, "
        "  stale, risk_grade) "
        "VALUES "
        "('m1','repo','main','code_snippet','foo.py','fn','C','k','u',1,1,1,0,NULL),"
        "('m2','repo','main','code_snippet','bar.py','gn',NULL,'k2','u2',1,2,2,1,'WILL_BREAK')"
    )
    storage.upsert_branch_state(
        BranchState(repo_id="repo", branch="main", last_indexed_sha=SHA, parent_sha=None)
    )
    storage.insert_relations([
        Relation(
            id="r1", repo_id="repo", branch="main",
            source_memory_id="m1", target_memory_id="m2", type="CALLS",
            confidence="EXTRACTED", created_at_sha=SHA, community=0,
        ),
        Relation(
            id="r2", repo_id="repo", branch="main",
            source_memory_id="m2", target_memory_id="m1", type="USES",
            confidence="EXTRACTED", created_at_sha=SHA, community=0,
        ),
    ])
    storage.conn.commit()


def test_t031_repo_stats(tmp_path: Path) -> None:
    storage = StorageManager(base_dir=tmp_path / "store")
    _seed(storage)
    mm = _StubMM(storage)
    payload = json.loads(resources.repo_stats(mm, "repo"))
    assert payload["memories_total"] == 2
    assert payload["memories_stale"] == 1
    assert payload["stale_fraction"] == 0.5
    assert payload["relations_total"] == 2
    assert payload["community_count"] == 1
    assert payload["branches"] == [{"branch": "main", "last_indexed_sha": SHA}]


def test_t031_repo_stale_lists_only_stale(tmp_path: Path) -> None:
    storage = StorageManager(base_dir=tmp_path / "store")
    _seed(storage)
    mm = _StubMM(storage)
    payload = json.loads(resources.repo_stale(mm, "repo"))
    assert payload["count"] == 1
    assert payload["items"][0]["id"] == "m2"
    assert payload["items"][0]["risk_grade"] == "WILL_BREAK"


def test_t031_branch_summary(tmp_path: Path) -> None:
    storage = StorageManager(base_dir=tmp_path / "store")
    _seed(storage)
    mm = _StubMM(storage)
    payload = json.loads(resources.branch_summary(mm, "repo", "main"))
    assert payload["memories"] == 2
    assert payload["relations"] == 2
    assert payload["last_indexed_sha"] == SHA


def test_t031_community_summary(tmp_path: Path) -> None:
    storage = StorageManager(base_dir=tmp_path / "store")
    _seed(storage)
    mm = _StubMM(storage)
    payload = json.loads(resources.community_summary(mm, "repo", 0))
    assert payload["member_count"] == 2
    assert set(payload["members"]) == {"m1", "m2"}
    # Top-degree reports both nodes (both have edges in both directions).
    assert any(d["memory_id"] in {"m1", "m2"} for d in payload["top_degree"])


def test_t031_payload_under_4kb(tmp_path: Path) -> None:
    """Even with 200 stale rows the payload stays under the 4 KB cap and
    flags truncation."""
    storage = StorageManager(base_dir=tmp_path / "store")
    for i in range(200):
        storage.conn.execute(
            "INSERT INTO memories (id, repo_id, branch_name, content_type, "
            "  file_path, function_name, "
            "  checksum, content_ref, token_count, created_at, updated_at, stale) "
            f"VALUES ('m{i}', 'repo', 'main', 'code_snippet', "
            f"'src/file_{i}.py', 'fn_{i}', 'k', 'u', 1, {i}, {i}, 1)"
        )
    storage.conn.commit()
    mm = _StubMM(storage)
    raw = resources.repo_stale(mm, "repo")
    assert len(raw) <= 4096
    payload = json.loads(raw)
    assert payload.get("truncated") is True


# ---------- T037: secrets-detection audit on merge_branch -------------------


def test_t037_merge_branch_response_has_skipped_secrets_field() -> None:
    """FR-034 is honored on the merge_branch path — the response schema
    surfaces a count of memories that were dropped because their content
    blob contained secrets."""
    from mememo.tools.merge_branch import MergeBranchResponse

    r = MergeBranchResponse(success=True, message="ok", skipped_secrets_count=3)
    assert r.skipped_secrets_count == 3
    r2 = MergeBranchResponse(success=True, message="ok")
    assert r2.skipped_secrets_count == 0
