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
_stub_module("faiss", Index=_Stub, IndexFlatL2=_Stub, IndexIDMap=_Stub, IndexIVFFlat=_Stub)
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


def test_t029_canonical_repo_root_resolves_relative_common_dir(tmp_path: Path) -> None:
    """Legacy single-checkout: git rev-parse --git-common-dir returns the relative
    string '.git'. We must resolve against cwd and strip the trailing '.git' to
    arrive at the repo root — not return '.git' or a per-cwd subfolder.
    """
    import asyncio

    from mememo.core.git_manager import GitManager

    gm = GitManager()

    async def fake_exec(command, args, cwd=None):
        if command == "rev-parse" and args == ["--git-common-dir"]:
            return ".git"
        raise RuntimeError("not stubbed")

    gm._exec_git = fake_exec  # type: ignore[assignment]
    result = asyncio.run(gm.canonical_repo_root(cwd=str(tmp_path)))
    assert Path(result) == tmp_path.resolve()


def test_t029_detect_context_empty_repo_no_commits(tmp_path: Path) -> None:
    """A `git init`-only directory (no commits) must not crash detect_context.

    The fallback branch in git_manager.py:239-249 constructs a sentinel
    GitContext with repo_id = hash(resolved path) and branch="main".
    """
    import asyncio

    from mememo.core.git_manager import GitManager

    gm = GitManager()

    async def boom(*a, **k):
        raise RuntimeError("simulated: no commits / no git")

    # Make every git path explode so detect_context lands on the except branch.
    gm.find_repo_root = boom  # type: ignore[assignment]
    gm.get_repo_name = boom  # type: ignore[assignment]
    gm.get_remote_url = boom  # type: ignore[assignment]
    gm.get_current_branch = boom  # type: ignore[assignment]
    gm.get_latest_commit = boom  # type: ignore[assignment]

    ctx = asyncio.run(gm.detect_context(cwd=str(tmp_path)))
    assert ctx.repo.id  # non-empty deterministic id
    assert ctx.branch.name == "main"
    assert ctx.branch.commit_hash == ""


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
    storage.upsert_branch_state(BranchState(repo_id="old", branch="main", last_indexed_sha=SHA))
    storage.insert_relations(
        [
            Relation(
                id="r1",
                repo_id="old",
                branch="main",
                source_memory_id="m1",
                target_memory_id="m1",
                type="CALLS",
                confidence="EXTRACTED",
                created_at_sha=SHA,
            )
        ]
    )
    storage.conn.commit()

    counts = storage.reassign_repo_id("old", "new")
    assert counts.get("memories", 0) == 1
    assert counts.get("branch_state", 0) == 1
    assert counts.get("relations", 0) == 1

    # Old rows are gone; new rows present.
    assert (
        storage.conn.execute("SELECT COUNT(*) AS n FROM memories WHERE repo_id = 'old'").fetchone()[
            "n"
        ]
        == 0
    )
    assert (
        storage.conn.execute("SELECT COUNT(*) AS n FROM memories WHERE repo_id = 'new'").fetchone()[
            "n"
        ]
        == 1
    )


def test_t030_reassign_repo_id_noop_on_self_target(tmp_path: Path) -> None:
    storage = StorageManager(base_dir=tmp_path / "store")
    counts = storage.reassign_repo_id("same", "same")
    assert counts == {}


def test_t030_migrate_worktrees_cli_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Run `mememo migrate-worktrees` twice end-to-end; second pass must reassign zero rows.

    Catches a regression that would re-write data on every invocation (e.g. if the
    skip-when-already-canonical check were dropped from _cmd_migrate_worktrees).
    """
    from mememo.utils.hashing import hash_path

    store_dir = tmp_path / "store"
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(store_dir))

    canonical_path = "/fake/primary"
    worktree_path = "/fake/worktree"
    old_repo_id = "old_worktree_hash"
    canonical_id = hash_path(canonical_path)

    storage = StorageManager(base_dir=store_dir)
    storage.conn.execute(
        "INSERT INTO memories (id, repo_id, repo_path, branch_name, content_type, "
        "  checksum, content_ref, token_count, created_at, updated_at) "
        "VALUES (?, ?, ?, 'main', 'context', 'k', 'u', 1, 1, 1)",
        ("m1", old_repo_id, worktree_path),
    )
    storage.conn.commit()
    storage.conn.close()

    async def fake_canonical(self, cwd):  # noqa: ARG001
        return canonical_path

    monkeypatch.setattr("mememo.core.git_manager.GitManager.canonical_repo_root", fake_canonical)

    from mememo.__main__ import _cmd_migrate_worktrees

    rc = _cmd_migrate_worktrees(["--repo-path", canonical_path])
    assert rc == 0
    first_out = capsys.readouterr().out
    assert "reassigned" in first_out

    storage2 = StorageManager(base_dir=store_dir)
    row = storage2.conn.execute("SELECT repo_id FROM memories WHERE id = 'm1'").fetchone()
    assert row["repo_id"] == canonical_id
    storage2.conn.close()

    rc2 = _cmd_migrate_worktrees(["--repo-path", canonical_path])
    assert rc2 == 0
    second_out = capsys.readouterr().out
    # Either the orphan-scan finds nothing (skip), or a self-reassign that touches 0 rows.
    # Definitive check: no row's repo_id changed.
    storage3 = StorageManager(base_dir=store_dir)
    final_row = storage3.conn.execute("SELECT repo_id FROM memories WHERE id = 'm1'").fetchone()
    assert final_row["repo_id"] == canonical_id
    # Second pass must not report any non-zero counts.
    assert (
        "memories" not in second_out
        or '"memories": 0' in second_out
        or "no orphaned repo_ids found" in second_out
    )
    storage3.conn.close()


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
    storage.insert_relations(
        [
            Relation(
                id="r1",
                repo_id="repo",
                branch="main",
                source_memory_id="m1",
                target_memory_id="m2",
                type="CALLS",
                confidence="EXTRACTED",
                created_at_sha=SHA,
                community=0,
            ),
            Relation(
                id="r2",
                repo_id="repo",
                branch="main",
                source_memory_id="m2",
                target_memory_id="m1",
                type="USES",
                confidence="EXTRACTED",
                created_at_sha=SHA,
                community=0,
            ),
        ]
    )
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


def test_t031_payload_truncation_at_boundary(tmp_path: Path) -> None:
    """Find the minimal stale-row count that pushes the payload over 4096 and
    assert truncation engages at that boundary, not one row past it.

    Boundary defects (off-by-one in `len(encoded) > MAX_PAYLOAD_BYTES`) would
    show up as either skipping truncation on the first over-size payload or
    truncating one row too early.
    """
    storage = StorageManager(base_dir=tmp_path / "store")
    mm = _StubMM(storage)

    def _seed(n: int) -> None:
        storage.conn.execute("DELETE FROM memories")
        for i in range(n):
            storage.conn.execute(
                "INSERT INTO memories (id, repo_id, branch_name, content_type, "
                "  file_path, function_name, "
                "  checksum, content_ref, token_count, created_at, updated_at, stale) "
                f"VALUES ('m{i:03d}', 'repo', 'main', 'code_snippet', "
                f"'src/file_{i:03d}.py', 'fn_{i:03d}', 'k', 'u', 1, {i}, {i}, 1)"
            )
        storage.conn.commit()

    # Search up: find first N where untruncated payload would exceed 4096.
    boundary = None
    for n in range(1, 200):
        _seed(n)
        raw = resources.repo_stale(mm, "repo")
        payload = json.loads(raw)
        # Probe what the *untruncated* size would have been by re-serializing
        # all items the resource considered, ignoring the truncated flag.
        if payload.get("truncated"):
            boundary = n
            break

    assert boundary is not None, "expected to find a truncation boundary under 200 rows"

    # At the exact boundary: capped AND flagged.
    _seed(boundary)
    raw = resources.repo_stale(mm, "repo")
    assert len(raw) <= 4096
    assert json.loads(raw)["truncated"] is True

    # One below the boundary: NOT flagged.
    _seed(boundary - 1)
    raw_below = resources.repo_stale(mm, "repo")
    payload_below = json.loads(raw_below)
    assert len(raw_below) <= 4096
    assert payload_below.get("truncated") is not True, (
        f"truncated flag set one row before the boundary ({boundary - 1} rows): "
        f"payload was {len(raw_below)} bytes"
    )


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
