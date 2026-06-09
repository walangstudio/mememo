"""Tests for mememo/tools/comprehension.py — ask (cited Q&A) + overview (system map).

ask is tested with a stubbed search_similar (no embedding model needed); overview
is tested against a real seeded StorageManager (raw graph, no model).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mememo.core.llm_adapter import LLMAdapter
from mememo.core.storage_manager import StorageManager
from mememo.tools.comprehension import (
    AskParams,
    OverviewParams,
    ask,
    overview,
)
from mememo.types.memory import (
    BranchContext,
    Memory,
    MemoryContent,
    MemoryMetadata,
    MemoryRelationships,
    MemorySummary,
    RepoContext,
    SearchResult,
)

SHA = "a" * 40
REPO = "test-repo"
BRANCH = "main"


# ---------- ask -------------------------------------------------------------


def _mem(text, file_path, line_range, fn=None, cn=None) -> Memory:
    return Memory(
        id=f"m-{file_path}-{fn or cn}",
        repo=RepoContext(id=REPO, name="x", path="/x"),
        branch=BranchContext(name=BRANCH, commit_hash=""),
        content=MemoryContent(
            type="code_snippet",
            text=text,
            file_path=file_path,
            line_range=line_range,
            function_name=fn,
            class_name=cn,
        ),
        metadata=MemoryMetadata(checksum="c", token_count=1),
        relationships=MemoryRelationships(),
        summary=MemorySummary(one_line="x"),
    )


class _FakeMM:
    """Stub memory_manager exposing only the async search_similar ask() uses."""

    def __init__(self, results):
        self._results = results

    async def search_similar(self, params):
        return self._results


def _passthrough_adapter() -> LLMAdapter:
    # No providers.yaml / API key -> default_provider 'passthrough'.
    return LLMAdapter(config_path=Path("does-not-exist.yaml"))


@pytest.mark.asyncio
async def test_ask_passthrough_builds_cited_prompt():
    results = [
        SearchResult(
            memory=_mem("def parse(argv): ...", "cli.py", (10, 20), fn="parse"), similarity=0.4
        ),
        SearchResult(
            memory=_mem("class Config: ...", "config.py", (1, 8), cn="Config"), similarity=0.3
        ),
    ]
    resp = await ask(
        AskParams(question="how are args parsed", repo_id=REPO, branch=BRANCH),
        _FakeMM(results),
        _passthrough_adapter(),
    )
    assert resp.success and resp.passthrough
    assert len(resp.citations) == 2
    assert resp.citations[0].index == 1
    assert resp.citations[0].file == "cli.py" and resp.citations[0].lines == "10-20"
    assert resp.citations[0].symbol == "parse"
    # Prompt carries the numbered anchors so the host can cite them.
    assert "[1] cli.py:10-20" in resp.passthrough_prompt
    assert "[2] config.py:1-8" in resp.passthrough_prompt
    assert "how are args parsed" in resp.passthrough_prompt


@pytest.mark.asyncio
async def test_ask_empty_returns_failure():
    resp = await ask(
        AskParams(question="anything", repo_id=REPO, branch=BRANCH),
        _FakeMM([]),
        _passthrough_adapter(),
    )
    assert resp.success is False
    assert "index" in resp.message.lower()


@pytest.mark.asyncio
async def test_ask_skips_empty_text_and_renumbers():
    # An empty-text chunk must be skipped (no blank [n]) without leaving a gap.
    results = [
        SearchResult(memory=_mem("", "blank.py", (1, 2), fn="blank"), similarity=0.5),
        SearchResult(memory=_mem("def real(): ...", "a.py", (3, 9), fn="real"), similarity=0.4),
        SearchResult(memory=_mem("def other(): ...", "b.py", (4, 6), fn="other"), similarity=0.3),
    ]
    resp = await ask(
        AskParams(question="q", repo_id=REPO, branch=BRANCH),
        _FakeMM(results),
        _passthrough_adapter(),
    )
    assert resp.success
    assert [c.index for c in resp.citations] == [1, 2]
    assert resp.citations[0].file == "a.py"  # the blank one was dropped
    assert "[1] a.py:3-9" in resp.passthrough_prompt
    assert "blank.py" not in resp.passthrough_prompt


@pytest.mark.asyncio
async def test_ask_all_empty_text_returns_failure():
    results = [SearchResult(memory=_mem("   ", "x.py", (1, 2), fn="x"), similarity=0.5)]
    resp = await ask(
        AskParams(question="q", repo_id=REPO, branch=BRANCH),
        _FakeMM(results),
        _passthrough_adapter(),
    )
    assert resp.success is False
    assert "readable source text" in resp.message


@pytest.mark.asyncio
async def test_ask_truncates_oversized_grounding():
    big = "x" * 5000
    results = [
        SearchResult(memory=_mem(big, f"f{i}.py", (1, 50), fn=f"fn{i}"), similarity=0.2)
        for i in range(6)
    ]
    resp = await ask(
        AskParams(question="q", repo_id=REPO, branch=BRANCH, top_k=6),
        _FakeMM(results),
        _passthrough_adapter(),
    )
    assert resp.success and resp.truncated
    # Budget stops well short of all 6 oversized chunks.
    assert len(resp.citations) < 6


# ---------- overview --------------------------------------------------------


def _seed(storage: StorageManager) -> None:
    storage.conn.executemany(
        "INSERT INTO memories (id, repo_id, branch_name, content_type, "
        "  file_path, function_name, class_name, language, chunk_type, "
        "  checksum, content_ref, token_count, created_at, updated_at, stale) "
        "VALUES (?, ?, ?, 'code_snippet', ?, ?, ?, ?, ?, ?, ?, 1, 1, 1, 0)",
        [
            ("a-cls", REPO, BRANCH, "core/a.py", None, "Engine", "python", "class", "c1", "r1"),
            ("a-run", REPO, BRANCH, "core/a.py", "run", "Engine", "python", "method", "c2", "r2"),
            ("b-fn", REPO, BRANCH, "core/b.py", "helper", None, "python", "function", "c3", "r3"),
            ("c-fn", REPO, BRANCH, "web/c.py", "handler", None, "python", "function", "c4", "r4"),
        ],
    )
    storage.conn.executemany(
        "INSERT INTO relations (id, repo_id, branch, source_memory_id, "
        "  target_memory_id, target_symbol, type, confidence, created_at_sha, stale) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'EXTRACTED', ?, 0)",
        [
            # web/c.py handler CALLS core/b.py helper (twice -> helper is the hot symbol)
            ("r-call1", REPO, BRANCH, "c-fn", "b-fn", None, "CALLS", SHA),
            ("r-call2", REPO, BRANCH, "a-run", "b-fn", None, "CALLS", SHA),
            # web/c.py IMPORTS core/b.py -> cross-subsystem edge for the diagram
            ("r-imp", REPO, BRANCH, "c-fn", "b-fn", None, "IMPORTS", SHA),
        ],
    )
    storage.conn.commit()


class _FakeMMStore:
    def __init__(self, storage):
        self.storage_manager = storage


@pytest.fixture()
def store(tmp_path: Path) -> StorageManager:
    s = StorageManager(base_dir=tmp_path / "store")
    _seed(s)
    return s


@pytest.mark.asyncio
async def test_overview_passthrough_has_facts(store: StorageManager):
    resp = await overview(
        OverviewParams(repo_id=REPO, branch=BRANCH, depth=2),
        _FakeMMStore(store),
        _passthrough_adapter(),
    )
    assert resp.success and resp.passthrough
    names = {s.name for s in resp.subsystems}
    assert "core" in names and "web" in names
    assert resp.languages.get("python") == 4
    assert resp.edge_counts.get("CALLS") == 2
    # helper is called twice -> it leads the core-API list.
    assert any("helper" in s for s in resp.key_symbols)
    assert "Subsystems" in resp.passthrough_prompt
    # web/c.py IMPORTS core/b.py is a cross-subsystem edge -> a real diagram.
    assert resp.mermaid.startswith("flowchart")


@pytest.mark.asyncio
async def test_overview_truncated_when_subsystems_capped(store: StorageManager):
    # max_nodes=1 keeps only the largest subsystem; truncated must flag the drop.
    resp = await overview(
        OverviewParams(repo_id=REPO, branch=BRANCH, depth=2, max_nodes=1),
        _FakeMMStore(store),
        _passthrough_adapter(),
    )
    assert resp.success and resp.truncated
    assert len(resp.subsystems) == 1


@pytest.mark.asyncio
async def test_overview_no_repo_context_returns_failure():
    # No repo_id/branch and no git_manager on the stub -> empty lane, clear message.
    resp = await overview(
        OverviewParams(),
        _FakeMMStore(None),
        _passthrough_adapter(),
    )
    assert resp.success is False
    assert "repo context" in resp.message.lower()


@pytest.mark.asyncio
async def test_overview_empty_returns_failure(tmp_path: Path):
    empty = StorageManager(base_dir=tmp_path / "empty")
    resp = await overview(
        OverviewParams(repo_id=REPO, branch=BRANCH),
        _FakeMMStore(empty),
        _passthrough_adapter(),
    )
    assert resp.success is False
    assert "index" in resp.message.lower()
