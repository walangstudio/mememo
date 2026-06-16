"""enrich_docstrings: find undocumented symbols and write doc comments for them.

Uses a real StorageManager (the tool reads the memories table + content blobs
to tell documented from undocumented), a SimpleNamespace memory_manager
exposing it, and a fake passthrough/LLM adapter. resolve_repo_branch
short-circuits on the explicit repo_id/branch, so no git context is needed.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from mememo.core.storage_manager import StorageManager
from mememo.tools.comprehension import EnrichParams, enrich_docstrings
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


def _code_mem(repo_id, file_path, *, func=None, cls=None, lang, text, docstring):
    return Memory(
        id=str(uuid.uuid4()),
        repo=RepoContext(id=repo_id, name="r", path=str(Path.cwd()), remote_url=None),
        branch=BranchContext(name="main", commit_hash=NULL_SHA),
        content=MemoryContent(
            type="code_snippet",
            text=text,
            docstring=docstring,
            language=lang,
            file_path=file_path,
            function_name=func,
            class_name=cls,
        ),
        metadata=MemoryMetadata(
            checksum=calculate_checksum(f"{file_path}:{func or cls}:{text}:{docstring or ''}"),
            token_count=len(text.split()),
            created_at_sha=NULL_SHA,
            updated_at_sha=NULL_SHA,
        ),
        relationships=MemoryRelationships(),
        summary=MemorySummary(one_line="x"),
    )


def _passthrough_adapter():
    return SimpleNamespace(is_passthrough=lambda: True)


def _llm_adapter(answer):
    async def complete(_system, _user):
        return answer

    return SimpleNamespace(is_passthrough=lambda: False, complete=complete)


async def _store(tmp_path, mems):
    storage = StorageManager(base_dir=tmp_path / "store")
    for m in mems:
        await storage.save_memory(m)
    return SimpleNamespace(storage_manager=storage)


@pytest.mark.asyncio
async def test_lists_only_undocumented_passthrough(tmp_path) -> None:
    mm = await _store(
        tmp_path,
        [
            _code_mem("r1", "a.rs", func="add", lang="rust", text="fn add() {}", docstring="Adds."),
            _code_mem("r1", "a.rs", cls="Pt", lang="rust", text="struct Pt;", docstring="A point."),
            _code_mem(
                "r1",
                "b.rs",
                func="frobnicate",
                lang="rust",
                text="fn frobnicate() {}",
                docstring=None,
            ),
        ],
    )
    resp = await enrich_docstrings(
        EnrichParams(repo_id="r1", branch="main"), mm, _passthrough_adapter()
    )
    assert resp.success
    assert resp.documented == 2
    assert resp.undocumented == 1
    assert resp.coverage == round(2 / 3, 3)
    assert [s.symbol for s in resp.symbols] == ["frobnicate"]
    assert resp.passthrough is True
    # The undocumented symbol's name + source are handed to the host model;
    # the two documented symbols are not.
    assert "frobnicate" in resp.passthrough_prompt
    assert "add" not in [s.symbol for s in resp.symbols]


@pytest.mark.asyncio
async def test_all_documented_nothing_to_do(tmp_path) -> None:
    mm = await _store(
        tmp_path,
        [
            _code_mem("r1", "a.rs", func="add", lang="rust", text="fn add() {}", docstring="Adds."),
        ],
    )
    resp = await enrich_docstrings(
        EnrichParams(repo_id="r1", branch="main"), mm, _passthrough_adapter()
    )
    assert resp.success
    assert resp.undocumented == 0
    assert resp.symbols == []
    assert resp.passthrough is False
    assert "nothing to enrich" in resp.message


@pytest.mark.asyncio
async def test_language_filter(tmp_path) -> None:
    mm = await _store(
        tmp_path,
        [
            _code_mem("r1", "a.rs", func="r_undoc", lang="rust", text="fn r() {}", docstring=None),
            _code_mem("r1", "a.go", func="g_undoc", lang="go", text="func g() {}", docstring=None),
        ],
    )
    resp = await enrich_docstrings(
        EnrichParams(repo_id="r1", branch="main", language="go"), mm, _passthrough_adapter()
    )
    assert [s.symbol for s in resp.symbols] == ["g_undoc"]
    assert resp.scanned == 1  # the rust symbol was filtered out before the scan


@pytest.mark.asyncio
async def test_empty_repo(tmp_path) -> None:
    mm = await _store(tmp_path, [])
    resp = await enrich_docstrings(
        EnrichParams(repo_id="r1", branch="main"), mm, _passthrough_adapter()
    )
    assert resp.success is False
    assert "index the repo" in resp.message.lower()


@pytest.mark.asyncio
async def test_unreadable_blob_not_reported_as_documented(tmp_path) -> None:
    # A matched row whose content blob is gone must not be counted as documented.
    mem = _code_mem("r1", "b.rs", func="frob", lang="rust", text="fn frob() {}", docstring=None)
    storage = StorageManager(base_dir=tmp_path / "store")
    await storage.save_memory(mem)
    ref = storage.conn.execute(
        "SELECT content_ref FROM memories WHERE id = ?", (mem.id,)
    ).fetchone()["content_ref"]
    (storage.base_dir / ref).unlink()  # delete the blob
    mm = SimpleNamespace(storage_manager=storage)
    resp = await enrich_docstrings(
        EnrichParams(repo_id="r1", branch="main"), mm, _passthrough_adapter()
    )
    assert resp.success is False
    assert resp.unreadable == 1
    assert "readable content" in resp.message


@pytest.mark.asyncio
async def test_llm_path_returns_answer(tmp_path) -> None:
    mm = await _store(
        tmp_path,
        [_code_mem("r1", "b.rs", func="frob", lang="rust", text="fn frob() {}", docstring=None)],
    )
    resp = await enrich_docstrings(
        EnrichParams(repo_id="r1", branch="main"), mm, _llm_adapter("/// Frobs the thing.")
    )
    assert resp.success
    assert resp.passthrough is False
    assert resp.answer == "/// Frobs the thing."
