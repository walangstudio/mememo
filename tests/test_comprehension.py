"""Tests for mememo/tools/comprehension.py — ask (cited Q&A) + overview (system map).

ask is tested with a stubbed search_similar (no embedding model needed); overview
is tested against a real seeded StorageManager (raw graph, no model).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mememo.core.llm_adapter import LLMAdapter
from mememo.core.storage_manager import StorageManager
from mememo.tools.comprehension import (
    AskParams,
    ExploreParams,
    OverviewParams,
    ProjectPromptParams,
    WikiParams,
    ask,
    explore,
    generate_wiki,
    overview,
    project_prompt,
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


# ---------- generate_wiki ---------------------------------------------------


@pytest.mark.asyncio
async def test_wiki_passthrough_has_plan_and_diagrams(store: StorageManager):
    resp = await generate_wiki(
        WikiParams(repo_id=REPO, branch=BRANCH, depth=2),
        _FakeMMStore(store),
        _passthrough_adapter(),
    )
    assert resp.success and resp.passthrough
    assert resp.wiki == "" and resp.written_to == ""
    assert resp.sections[0] == "Overview" and "Core API" in resp.sections
    # web/c.py IMPORTS core/b.py -> a real overview diagram is grounded for embedding.
    assert "overview" in resp.diagrams
    assert "Structural facts" in resp.passthrough_prompt
    assert "Markdown" in resp.passthrough_prompt
    # Architecture is a host-drawn high-level flow for non-technical readers; the
    # deterministic graphs ship as an engineers-only appendix.
    assert "flowchart TD" in resp.passthrough_prompt
    assert "non-technical" in resp.passthrough_prompt
    assert "Appendix: Module map" in resp.sections
    assert "Appendix: Module map" in resp.passthrough_prompt


@pytest.mark.asyncio
async def test_wiki_scope_filters_subsystems(store: StorageManager):
    ok = await generate_wiki(
        WikiParams(repo_id=REPO, branch=BRANCH, scope="core"),
        _FakeMMStore(store),
        _passthrough_adapter(),
    )
    assert ok.success
    # Facts list the scoped subsystem only; the dropped one has no "- web:" entry.
    assert "- core:" in ok.passthrough_prompt and "- web:" not in ok.passthrough_prompt
    # A scoped page drops the whole-repo diagrams (they'd show other subsystems),
    # so there's no engineers appendix either.
    assert ok.diagrams == {}
    assert "Appendix: Module map" not in ok.sections

    miss = await generate_wiki(
        WikiParams(repo_id=REPO, branch=BRANCH, scope="zzz-nope"),
        _FakeMMStore(store),
        _passthrough_adapter(),
    )
    assert miss.success is False
    assert "no subsystem matches" in miss.message.lower()


class _StubLLM:
    """Configured (non-passthrough) adapter that returns a canned wiki."""

    def is_passthrough(self):
        return False

    async def complete(self, system, user):
        return "# Wiki\nhello"


@pytest.mark.asyncio
async def test_wiki_writes_within_repo_root(store: StorageManager, tmp_path: Path):
    resp = await generate_wiki(
        WikiParams(repo_id=REPO, branch=BRANCH, repo_path=str(tmp_path), write_path="WIKI.md"),
        _FakeMMStore(store),
        _StubLLM(),
    )
    assert resp.success and not resp.passthrough
    assert resp.wiki.startswith("# Wiki")
    out = tmp_path / "WIKI.md"
    assert out.exists() and out.read_text(encoding="utf-8").startswith("# Wiki")
    assert resp.written_to == str(out.resolve())


@pytest.mark.asyncio
async def test_wiki_write_path_escape_refused(store: StorageManager, tmp_path: Path):
    resp = await generate_wiki(
        WikiParams(repo_id=REPO, branch=BRANCH, repo_path=str(tmp_path), write_path="../escape.md"),
        _FakeMMStore(store),
        _StubLLM(),
    )
    assert resp.success  # wiki is still generated
    assert resp.written_to == ""
    assert "outside" in resp.message.lower()
    assert not (tmp_path.parent / "escape.md").exists()


@pytest.mark.asyncio
async def test_wiki_no_repo_context_returns_failure():
    resp = await generate_wiki(
        WikiParams(),
        _FakeMMStore(None),
        _passthrough_adapter(),
    )
    assert resp.success is False
    assert "repo context" in resp.message.lower()


@pytest.mark.asyncio
async def test_wiki_empty_returns_failure(tmp_path: Path):
    empty = StorageManager(base_dir=tmp_path / "empty2")
    resp = await generate_wiki(
        WikiParams(repo_id=REPO, branch=BRANCH),
        _FakeMMStore(empty),
        _passthrough_adapter(),
    )
    assert resp.success is False
    assert "index" in resp.message.lower()


# ---------- explore ---------------------------------------------------------


def _seed_chain(storage: StorageManager) -> None:
    """handler -CALLS-> helper <-CALLS- run, with readable source blobs on disk."""
    storage.conn.executemany(
        "INSERT INTO memories (id, repo_id, branch_name, content_type, "
        "  file_path, function_name, class_name, language, chunk_type, "
        "  checksum, content_ref, token_count, created_at, updated_at, stale) "
        "VALUES (?, ?, ?, 'code_snippet', ?, ?, ?, ?, ?, ?, ?, 1, 1, 1, 0)",
        [
            ("c-fn", REPO, BRANCH, "web/c.py", "handler", None, "python", "function", "c1", "rc"),
            ("b-fn", REPO, BRANCH, "core/b.py", "helper", None, "python", "function", "c2", "rb"),
            ("a-run", REPO, BRANCH, "core/a.py", "run", "Engine", "python", "method", "c3", "ra"),
        ],
    )
    storage.conn.executemany(
        "INSERT INTO relations (id, repo_id, branch, source_memory_id, "
        "  target_memory_id, target_symbol, type, confidence, created_at_sha, stale) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'EXTRACTED', ?, 0)",
        [
            ("e1", REPO, BRANCH, "c-fn", "b-fn", None, "CALLS", SHA),
            ("e2", REPO, BRANCH, "a-run", "b-fn", None, "CALLS", SHA),
        ],
    )
    storage.conn.commit()
    for ref, text in (
        ("rc", "def handler(req): return helper(req)"),
        ("rb", "def helper(req): return Engine().run(req)"),
        ("ra", "def run(self, req): ..."),
    ):
        (storage.base_dir / ref).write_text(json.dumps({"text": text}), encoding="utf-8")


def _result(mid: str, sim: float) -> SearchResult:
    """A SearchResult whose memory.id matches a seeded store id (explore reads .id/.similarity)."""
    return SearchResult(
        memory=_mem("", "x.py", (1, 2), fn="x").model_copy(update={"id": mid}), similarity=sim
    )


class _FakeMMExplore:
    """Stub exposing storage_manager (traversal) + a two-call search_similar (seed, rerank)."""

    def __init__(self, storage, seed, rerank=None):
        self.storage_manager = storage
        self._seed = seed
        self._rerank = seed if rerank is None else rerank
        self._calls = 0

    async def search_similar(self, params):
        self._calls += 1
        return self._seed if self._calls == 1 else self._rerank


@pytest.fixture()
def chain_store(tmp_path: Path) -> StorageManager:
    s = StorageManager(base_dir=tmp_path / "estore")
    _seed_chain(s)
    return s


@pytest.mark.asyncio
async def test_explore_walks_multihop_and_builds_trace(chain_store: StorageManager):
    mm = _FakeMMExplore(
        chain_store,
        seed=[_result("c-fn", 0.5)],  # seed at the handler
        rerank=[_result("b-fn", 0.6), _result("a-run", 0.55), _result("c-fn", 0.2)],
    )
    resp = await explore(
        ExploreParams(goal="how is a request handled", repo_id=REPO, branch=BRANCH, max_hops=3),
        mm,
        _passthrough_adapter(),
    )
    assert resp.success and resp.passthrough
    # Reached all three nodes via two hops from the seed.
    assert resp.nodes_visited == 3
    files = {c.file for c in resp.citations}
    assert {"web/c.py", "core/b.py", "core/a.py"} <= files
    # The trace records the CALLS edges, de-duplicated.
    assert resp.trace and all(h.type == "CALLS" for h in resp.trace)
    assert len({(h.source, h.target) for h in resp.trace}) == len(resp.trace)
    assert "--CALLS-->" in resp.passthrough_prompt
    assert "how is a request handled" in resp.passthrough_prompt
    assert "Traversal" in resp.passthrough_prompt


@pytest.mark.asyncio
async def test_explore_edge_type_filter_blocks_traversal(chain_store: StorageManager):
    # Only IMPORTS allowed, but the chain is all CALLS -> no hops, just the seed.
    mm = _FakeMMExplore(chain_store, seed=[_result("c-fn", 0.5)])
    resp = await explore(
        ExploreParams(goal="x", repo_id=REPO, branch=BRANCH, edge_types=["IMPORTS"]),
        mm,
        _passthrough_adapter(),
    )
    assert resp.success
    assert resp.nodes_visited == 1 and resp.trace == []


@pytest.mark.asyncio
async def test_explore_no_seed_returns_failure(chain_store: StorageManager):
    resp = await explore(
        ExploreParams(goal="nothing matches", repo_id=REPO, branch=BRANCH),
        _FakeMMExplore(chain_store, seed=[]),
        _passthrough_adapter(),
    )
    assert resp.success is False
    assert "index" in resp.message.lower()


@pytest.mark.asyncio
async def test_explore_no_repo_context_returns_failure():
    resp = await explore(
        ExploreParams(goal="x"),
        _FakeMMExplore(None, seed=[]),
        _passthrough_adapter(),
    )
    assert resp.success is False
    assert "repo context" in resp.message.lower()


# ---------- project_prompt --------------------------------------------------


@pytest.mark.asyncio
async def test_project_prompt_passthrough_has_facts(store: StorageManager):
    resp = await project_prompt(
        ProjectPromptParams(repo_id=REPO, branch=BRANCH, focus="testing conventions"),
        _FakeMMStore(store),
        _passthrough_adapter(),
    )
    assert resp.success and resp.passthrough
    assert resp.languages.get("python") == 4
    assert any("helper" in s for s in resp.key_symbols)
    assert "Structural facts" in resp.passthrough_prompt
    assert "primer" in resp.passthrough_prompt.lower()
    assert "Focus the primer on: testing conventions" in resp.passthrough_prompt


@pytest.mark.asyncio
async def test_project_prompt_with_llm_returns_prompt(store: StorageManager):
    class _Stub:
        def is_passthrough(self):
            return False

        async def complete(self, system, user):
            return "You are working on X."

    resp = await project_prompt(
        ProjectPromptParams(repo_id=REPO, branch=BRANCH),
        _FakeMMStore(store),
        _Stub(),
    )
    assert resp.success and not resp.passthrough
    assert resp.prompt == "You are working on X."


@pytest.mark.asyncio
async def test_project_prompt_no_repo_context_returns_failure():
    resp = await project_prompt(
        ProjectPromptParams(),
        _FakeMMStore(None),
        _passthrough_adapter(),
    )
    assert resp.success is False
    assert "repo context" in resp.message.lower()


@pytest.mark.asyncio
async def test_project_prompt_empty_returns_failure(tmp_path: Path):
    empty = StorageManager(base_dir=tmp_path / "empty3")
    resp = await project_prompt(
        ProjectPromptParams(repo_id=REPO, branch=BRANCH),
        _FakeMMStore(empty),
        _passthrough_adapter(),
    )
    assert resp.success is False
    assert "index" in resp.message.lower()
