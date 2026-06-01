"""v0.5 batch 1 — Python AST edge emission + symbol resolver + relations table
+ graph_neighbors / graph_path traversal tools.

Covers T015 (Python edges), T019 (resolver), T020 (relations storage),
T023 (graph_neighbors), T024 (graph_path). Other v0.5 tasks (TS/JS/Go
edges, Leiden clustering, dedup pipeline, perf benchmarks) remain on
the failing-stub list under .claude/tests/.
"""

from __future__ import annotations

import sys
import types as _types
from pathlib import Path


# Stub heavy deps the same way the v0.4 test file does so this suite runs in
# any Python env.
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

import asyncio  # noqa: E402
import sqlite3  # noqa: E402

import pytest  # noqa: E402

from mememo.chunking.python_ast_chunker import (  # noqa: E402
    PythonASTChunker,
    file_path_to_module,
)
from mememo.core.storage_manager import StorageManager  # noqa: E402
from mememo.core.symbol_resolver import (  # noqa: E402
    SymbolEntry,
    resolve_edges,
)
from mememo.types import Relation  # noqa: E402

SHA = "a" * 40


# ---------- T015: Python AST edge emission ----------------------------------


PY_SAMPLE = """\
from foo.bar import baz

@decorator
class MyClass(BaseClass):
    def method(self):
        baz()
        self.attr_use = 1
"""


def test_t015_imports_calls_extends_uses_decorated_by_emitted() -> None:
    chunker = PythonASTChunker()
    chunks, edges = chunker.chunk_with_edges(PY_SAMPLE, "pkg/mod.py")

    by_type: dict[str, list] = {}
    for e in edges:
        by_type.setdefault(e.edge_type, []).append(e)

    assert any(e.target_label == "foo.bar.baz" for e in by_type.get("IMPORTS", []))
    assert any(e.target_label == "BaseClass" for e in by_type.get("EXTENDS", []))
    assert any(e.target_label == "decorator" for e in by_type.get("DECORATED_BY", []))
    assert any(e.target_label == "baz" for e in by_type.get("CALLS", []))
    assert any(e.target_label == "attr_use" for e in by_type.get("USES", []))


def test_t015_module_path_to_qualname() -> None:
    assert file_path_to_module("mememo/core/storage_manager.py") == "mememo.core.storage_manager"
    assert file_path_to_module("mememo\\core\\foo.py") == "mememo.core.foo"


def test_t015_source_qualname_includes_class_and_function() -> None:
    chunker = PythonASTChunker()
    _, edges = chunker.chunk_with_edges(PY_SAMPLE, "pkg/mod.py")
    calls = [e for e in edges if e.edge_type == "CALLS"]
    assert calls, "expected at least one CALLS edge"
    # The call to baz() lives inside pkg.mod.MyClass.method
    assert any(e.source_qualname.endswith("MyClass.method") for e in calls)


# ---------- T019: symbol resolver ------------------------------------------


def test_t019_exact_match_resolves_extracted() -> None:
    from mememo.chunking.base_chunker import RawEdge

    symbols = [
        SymbolEntry(memory_id="m-cls", qualname="pkg.mod.MyClass"),
        SymbolEntry(memory_id="m-method", qualname="pkg.mod.MyClass.method"),
        SymbolEntry(memory_id="m-baz", qualname="pkg.mod.foo.baz"),
    ]
    raw = [
        RawEdge("pkg.mod.MyClass.method", "pkg.mod.foo.baz", "CALLS"),
    ]
    relations = resolve_edges(raw, repo_id="r", branch="main", commit_sha=SHA, symbols=symbols)
    assert len(relations) == 1
    assert relations[0].confidence == "EXTRACTED"
    assert relations[0].target_memory_id == "m-baz"
    assert relations[0].target_symbol is None


def test_t019_suffix_match_resolves_extracted() -> None:
    from mememo.chunking.base_chunker import RawEdge

    symbols = [
        SymbolEntry(memory_id="m-method", qualname="pkg.mod.MyClass.method"),
        SymbolEntry(memory_id="m-baz", qualname="pkg.foo.baz"),
    ]
    raw = [RawEdge("pkg.mod.MyClass.method", "baz", "CALLS")]
    relations = resolve_edges(raw, repo_id="r", branch="main", commit_sha=SHA, symbols=symbols)
    assert relations[0].confidence == "EXTRACTED"
    assert relations[0].target_memory_id == "m-baz"


def test_t019_unresolved_is_ambiguous() -> None:
    from mememo.chunking.base_chunker import RawEdge

    symbols = [SymbolEntry(memory_id="m1", qualname="pkg.mod.MyClass.method")]
    raw = [RawEdge("pkg.mod.MyClass.method", "unknown_function", "CALLS")]
    relations = resolve_edges(raw, repo_id="r", branch="main", commit_sha=SHA, symbols=symbols)
    assert relations[0].confidence == "AMBIGUOUS"
    assert relations[0].target_memory_id is None
    assert relations[0].target_symbol == "unknown_function"


def test_t019_skips_when_source_qualname_unknown() -> None:
    from mememo.chunking.base_chunker import RawEdge

    raw = [RawEdge("nowhere.unknown", "anywhere", "CALLS")]
    relations = resolve_edges(raw, repo_id="r", branch="main", commit_sha=SHA, symbols=[])
    assert relations == []


# Regression: IMPORTS edges have source_qualname = module (e.g. "pkg.a") but
# the indexer only registered class/function qualnames like "pkg.a.foo".
# "pkg.a" was absent from the symbol table so resolve_edges silently dropped
# every IMPORTS edge.  Fix: _flush now registers a module-level SymbolEntry
# pointing to the first chunk of the file when no bare-module chunk exists.
def test_t019_imports_edge_resolves_when_module_symbol_registered() -> None:
    from mememo.chunking.base_chunker import RawEdge

    # Simulate what _flush now emits: one function-chunk symbol + a module symbol
    # pointing to the same memory (the first chunk of the file).
    symbols = [
        SymbolEntry(memory_id="m-func", qualname="pkg.a.foo"),
        SymbolEntry(memory_id="m-func", qualname="pkg.a"),  # module-level entry
        SymbolEntry(memory_id="m-bar", qualname="pkg.b.bar"),
    ]
    raw = [
        RawEdge("pkg.a", "os", "IMPORTS"),  # source = module
        RawEdge("pkg.a", "pathlib", "IMPORTS"),
        RawEdge("pkg.a.foo", "bar", "CALLS"),  # source = function qualname
    ]
    relations = resolve_edges(raw, repo_id="r", branch="main", commit_sha=SHA, symbols=symbols)
    imports = [r for r in relations if r.type == "IMPORTS"]
    calls = [r for r in relations if r.type == "CALLS"]
    assert len(imports) == 2, (
        f"Expected 2 IMPORTS relations, got {len(imports)}. "
        "Module symbol entry missing from symbol table?"
    )
    assert len(calls) == 1
    assert all(r.source_memory_id == "m-func" for r in imports)


# ---------- T020: relations storage CRUD -----------------------------------


def test_t020_relations_table_round_trip(tmp_path: Path) -> None:
    storage = StorageManager(base_dir=tmp_path / "store")
    r = Relation(
        id="r1",
        repo_id="repo",
        branch="main",
        source_memory_id="m1",
        target_memory_id="m2",
        type="CALLS",
        confidence="EXTRACTED",
        created_at_sha=SHA,
    )
    assert storage.insert_relations([r]) == 1
    rows = storage.list_relations(repo_id="repo", branch="main")
    assert len(rows) == 1
    assert rows[0].source_memory_id == "m1"
    assert rows[0].target_memory_id == "m2"
    assert rows[0].type == "CALLS"


def test_t020_edge_type_validated_by_pydantic_not_db(tmp_path: Path) -> None:
    # v0.7: the relations.type DB CHECK was dropped so new edge types
    # (DOCUMENTS, ...) need no DDL migration. Enforcement moved to the
    # RelationType Pydantic literal — that is where a bad type is rejected.
    from pydantic import ValidationError

    from mememo.types.memory import Relation

    with pytest.raises(ValidationError):
        Relation(
            id="x",
            repo_id="r",
            branch="main",
            source_memory_id="m",
            target_symbol="t",
            type="NOT_A_REAL_TYPE",  # type: ignore[arg-type]
            created_at_sha=SHA,
        )

    # The DB no longer rejects arbitrary type strings, and the new DOCUMENTS
    # edge type inserts cleanly.
    storage = StorageManager(base_dir=tmp_path / "store")
    storage.conn.execute(
        "INSERT INTO relations (id, repo_id, branch, source_memory_id, target_symbol, "
        "type, confidence, created_at_sha) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("d1", "r", "main", "m", "Foo", "DOCUMENTS", "INFERRED", SHA),
    )
    storage.conn.commit()
    row = storage.conn.execute("SELECT type FROM relations WHERE id='d1'").fetchone()
    assert row[0] == "DOCUMENTS"


def test_t020_legacy_relations_check_is_migrated(tmp_path: Path) -> None:
    # Simulate a pre-v0.7 DB whose relations table still carries the hardcoded
    # type CHECK, then confirm _migrate_schema rebuilds it (rows preserved, new
    # edge types insertable). This is the migration's highest-risk path.
    storage = StorageManager(base_dir=tmp_path / "store")
    storage.conn.executescript("""
        DROP TABLE relations;
        CREATE TABLE relations (
            id TEXT PRIMARY KEY, repo_id TEXT NOT NULL, branch TEXT NOT NULL,
            source_memory_id TEXT NOT NULL, target_memory_id TEXT, target_symbol TEXT,
            type TEXT NOT NULL CHECK (type IN ('IMPORTS','CALLS','EXTENDS','IMPLEMENTS','USES','DECORATED_BY')),
            confidence TEXT NOT NULL CHECK (confidence IN ('EXTRACTED','INFERRED','AMBIGUOUS')),
            created_at_sha TEXT NOT NULL CHECK (length(created_at_sha) = 40),
            stale INTEGER DEFAULT 0, community INTEGER
        );
        """)
    storage.conn.execute(
        "INSERT INTO relations (id, repo_id, branch, source_memory_id, target_symbol, "
        "type, confidence, created_at_sha) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("old", "r", "main", "m", "Bar", "CALLS", "EXTRACTED", SHA),
    )
    storage.conn.commit()

    storage._migrate_schema()  # idempotent rebuild

    assert (
        storage.conn.execute("SELECT type FROM relations WHERE id='old'").fetchone()[0] == "CALLS"
    )
    ddl = storage.conn.execute("SELECT sql FROM sqlite_master WHERE name='relations'").fetchone()[0]
    assert "CHECK (type IN" not in ddl
    storage.conn.execute(
        "INSERT INTO relations (id, repo_id, branch, source_memory_id, target_symbol, "
        "type, confidence, created_at_sha) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("new", "r", "main", "m", "Foo", "DOCUMENTS", "INFERRED", SHA),
    )
    storage.conn.commit()


def test_t020_sha_length_check(tmp_path: Path) -> None:
    with pytest.raises(Exception):
        # Pydantic catches it first
        Relation(
            id="r2",
            repo_id="r",
            branch="main",
            source_memory_id="m",
            type="CALLS",
            confidence="EXTRACTED",
            created_at_sha="short",
        )


def test_t020_sha_length_check_at_db_layer(tmp_path: Path) -> None:
    """Raw-SQL INSERT bypassing Pydantic must trip the DDL CHECK constraint.

    Catches a regression where the schema's CHECK(length(created_at_sha) = 40)
    is dropped or weakened — Pydantic-only validation would no longer save us.
    """
    storage = StorageManager(base_dir=tmp_path / "store")
    with pytest.raises(sqlite3.IntegrityError):
        storage.conn.execute(
            "INSERT INTO relations (id, repo_id, branch, source_memory_id, type, "
            "confidence, created_at_sha) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("rbad", "r", "main", "m", "CALLS", "EXTRACTED", "short"),
        )


# ---------- T023 / T024: graph traversal tools -----------------------------


def _seed_chain(storage: StorageManager) -> None:
    """Build a small chain a -> b -> c -> d (CALLS) plus a -> e (USES)."""
    rels = []
    for src, tgt, t in [
        ("a", "b", "CALLS"),
        ("b", "c", "CALLS"),
        ("c", "d", "CALLS"),
        ("a", "e", "USES"),
    ]:
        from uuid import uuid4

        rels.append(
            Relation(
                id=str(uuid4()),
                repo_id="r",
                branch="main",
                source_memory_id=src,
                target_memory_id=tgt,
                type=t,
                confidence="EXTRACTED",
                created_at_sha=SHA,
            )
        )
    storage.insert_relations(rels)


class _StubMM:
    def __init__(self, storage: StorageManager) -> None:
        self.storage_manager = storage


def test_t023_graph_neighbors_outbound_depth_1(tmp_path: Path) -> None:
    from mememo.tools.graph_neighbors import GraphNeighborsParams, graph_neighbors

    storage = StorageManager(base_dir=tmp_path / "store")
    _seed_chain(storage)
    mm = _StubMM(storage)
    resp = asyncio.run(
        graph_neighbors(GraphNeighborsParams(memory_id="a", direction="out", depth=1), mm)
    )
    assert set(resp.visited) == {"a", "b", "e"}
    assert len(resp.edges) == 2


def test_t023_graph_neighbors_filters_edge_types(tmp_path: Path) -> None:
    from mememo.tools.graph_neighbors import GraphNeighborsParams, graph_neighbors

    storage = StorageManager(base_dir=tmp_path / "store")
    _seed_chain(storage)
    mm = _StubMM(storage)
    resp = asyncio.run(
        graph_neighbors(
            GraphNeighborsParams(memory_id="a", direction="out", depth=1, edge_types=["USES"]),
            mm,
        )
    )
    assert {e.type for e in resp.edges} == {"USES"}
    assert "e" in resp.visited


def test_t023_graph_neighbors_zero_edge_seed(tmp_path: Path) -> None:
    """Seed with no inbound/outbound edges: returns only the seed, no edges, success."""
    from mememo.tools.graph_neighbors import GraphNeighborsParams, graph_neighbors

    storage = StorageManager(base_dir=tmp_path / "store")
    # No relations seeded — every node is isolated.
    mm = _StubMM(storage)
    resp = asyncio.run(
        graph_neighbors(GraphNeighborsParams(memory_id="lonely", direction="both", depth=3), mm)
    )
    assert resp.success
    assert resp.visited == ["lonely"]
    assert resp.edges == []


def test_t023_graph_neighbors_depth_3(tmp_path: Path) -> None:
    from mememo.tools.graph_neighbors import GraphNeighborsParams, graph_neighbors

    storage = StorageManager(base_dir=tmp_path / "store")
    _seed_chain(storage)
    mm = _StubMM(storage)
    resp = asyncio.run(
        graph_neighbors(GraphNeighborsParams(memory_id="a", direction="out", depth=3), mm)
    )
    # a, b, c, d (chain), plus e (USES).
    assert {"a", "b", "c", "d", "e"} <= set(resp.visited)


def test_t024_graph_path_finds_chain(tmp_path: Path) -> None:
    from mememo.tools.graph_path import GraphPathParams, graph_path

    storage = StorageManager(base_dir=tmp_path / "store")
    _seed_chain(storage)
    mm = _StubMM(storage)
    resp = asyncio.run(graph_path(GraphPathParams(source_id="a", target_id="d"), mm))
    assert resp.path == ["a", "b", "c", "d"]
    assert resp.length == 3


def test_t024_graph_path_returns_none_when_unreachable(tmp_path: Path) -> None:
    from mememo.tools.graph_path import GraphPathParams, graph_path

    storage = StorageManager(base_dir=tmp_path / "store")
    _seed_chain(storage)
    mm = _StubMM(storage)
    resp = asyncio.run(graph_path(GraphPathParams(source_id="d", target_id="a"), mm))
    assert resp.path is None
    assert resp.length is None


def test_t024_graph_path_max_depth_bound(tmp_path: Path) -> None:
    from mememo.tools.graph_path import GraphPathParams, graph_path

    storage = StorageManager(base_dir=tmp_path / "store")
    _seed_chain(storage)
    mm = _StubMM(storage)
    resp = asyncio.run(graph_path(GraphPathParams(source_id="a", target_id="d", max_depth=2), mm))
    assert resp.path is None  # 'd' is 3 hops from 'a'


# ---------- Server registration smoke --------------------------------------


def test_server_registers_v05_graph_tools() -> None:
    import importlib

    srv = importlib.import_module("mememo.server")
    for name in ("graph_neighbors", "graph_path", "graph_impact"):
        assert hasattr(srv, name)


# ---------- T015 follow-up: methods carry parent_class qualnames ------------


def test_method_chunk_has_parent_class_via_unified_walk() -> None:
    chunker = PythonASTChunker()
    chunks, _ = chunker.chunk_with_edges(PY_SAMPLE, "pkg/mod.py")
    methods = [c for c in chunks if c.chunk_type == "method"]
    assert methods, "expected at least one method chunk"
    assert all(m.parent_class == "MyClass" for m in methods)


# ---------- T022: entity dedup pipeline -------------------------------------


def test_t022_dedup_collapses_near_duplicates(tmp_path: Path) -> None:
    pytest.importorskip("rapidfuzz")
    from mememo.core.graph_analysis import DedupCandidate, dedup_entities

    storage = StorageManager(base_dir=tmp_path / "store")
    cands = [
        DedupCandidate(memory_id="m1", label="UserController"),
        DedupCandidate(memory_id="m2", label="UserControler"),  # one typo
        DedupCandidate(memory_id="m3", label="DatabaseService"),
        DedupCandidate(memory_id="m4", label="DatabaseServices"),  # plural variant
        DedupCandidate(memory_id="m5", label="Unrelated"),
    ]
    result = dedup_entities(storage, cands, threshold=0.93)
    # 3 canonical groups: {m1+m2}, {m3+m4}, {m5}
    assert result.canonical_count == 3
    assert result.alias_count == 2
    rows = storage.conn.execute("SELECT * FROM entity_aliases ORDER BY alias_label").fetchall()
    labels = {(r["canonical_memory_id"], r["alias_label"]) for r in rows}
    assert ("m3", "DatabaseServices") in labels or ("m4", "DatabaseService") in labels


# ---------- T021: clustering --------------------------------------------------


def test_t021_clustering_deterministic_with_seed(tmp_path: Path) -> None:
    pytest.importorskip("networkx")
    from mememo.core.graph_analysis import cluster_relations

    storage = StorageManager(base_dir=tmp_path / "store")
    _seed_chain(storage)
    # Add a parallel disconnected component to give Louvain a real choice.
    from uuid import uuid4

    storage.insert_relations(
        [
            Relation(
                id=str(uuid4()),
                repo_id="r",
                branch="main",
                source_memory_id="x",
                target_memory_id="y",
                type="CALLS",
                confidence="EXTRACTED",
                created_at_sha=SHA,
            ),
            Relation(
                id=str(uuid4()),
                repo_id="r",
                branch="main",
                source_memory_id="y",
                target_memory_id="z",
                type="CALLS",
                confidence="EXTRACTED",
                created_at_sha=SHA,
            ),
        ]
    )

    r1 = cluster_relations(storage, repo_id="r", branch="main", seed=42)
    r2 = cluster_relations(storage, repo_id="r", branch="main", seed=42)
    # Same seed -> identical community membership.
    assert r1.communities == r2.communities
    assert r1.modularity is not None
    # The chain {a,b,c,d,e} and the chain {x,y,z} should land in different communities.
    assert r1.communities["a"] != r1.communities["x"]


# ---------- T025: graph_impact ----------------------------------------------


def test_t025_graph_impact_downstream_cone(tmp_path: Path) -> None:
    from mememo.tools.graph_impact import GraphImpactParams, graph_impact

    storage = StorageManager(base_dir=tmp_path / "store")
    _seed_chain(storage)
    mm = _StubMM(storage)
    resp = asyncio.run(
        graph_impact(GraphImpactParams(memory_id="a", direction="downstream", max_depth=4), mm)
    )
    visited_ids = {m.memory_id for m in resp.impacted}
    # a's downstream cone: b, c, d, e.
    assert visited_ids == {"b", "c", "d", "e"}


def test_t025_graph_impact_min_confidence_filter(tmp_path: Path) -> None:
    from uuid import uuid4

    from mememo.tools.graph_impact import GraphImpactParams, graph_impact

    storage = StorageManager(base_dir=tmp_path / "store")
    storage.insert_relations(
        [
            Relation(
                id=str(uuid4()),
                repo_id="r",
                branch="main",
                source_memory_id="a",
                target_memory_id="b",
                type="CALLS",
                confidence="AMBIGUOUS",
                created_at_sha=SHA,
            ),
            Relation(
                id=str(uuid4()),
                repo_id="r",
                branch="main",
                source_memory_id="a",
                target_memory_id="c",
                type="CALLS",
                confidence="EXTRACTED",
                created_at_sha=SHA,
            ),
        ]
    )
    mm = _StubMM(storage)
    # min_confidence='INFERRED' excludes the AMBIGUOUS edge to b.
    resp = asyncio.run(
        graph_impact(GraphImpactParams(memory_id="a", min_confidence="INFERRED"), mm)
    )
    assert {m.memory_id for m in resp.impacted} == {"c"}


def test_t025_graph_impact_decorates_with_risk_grade(tmp_path: Path) -> None:
    from uuid import uuid4

    from mememo.tools.graph_impact import GraphImpactParams, graph_impact

    storage = StorageManager(base_dir=tmp_path / "store")
    # Seed a memory row for 'b' with risk_grade=WILL_BREAK.
    storage.conn.execute(
        "INSERT INTO memories (id, repo_id, branch_name, content_type, file_path, "
        "checksum, content_ref, token_count, created_at, updated_at, risk_grade) "
        "VALUES ('b', 'r', 'main', 'code_snippet', 'foo.py', 'k', 'u', 1, 1, 1, 'WILL_BREAK')"
    )
    storage.conn.commit()
    storage.insert_relations(
        [
            Relation(
                id=str(uuid4()),
                repo_id="r",
                branch="main",
                source_memory_id="a",
                target_memory_id="b",
                type="CALLS",
                confidence="EXTRACTED",
                created_at_sha=SHA,
            )
        ]
    )
    mm = _StubMM(storage)
    resp = asyncio.run(graph_impact(GraphImpactParams(memory_id="a"), mm))
    assert resp.impacted[0].risk_grade == "WILL_BREAK"
    assert resp.impacted[0].file_path == "foo.py"


# ---------- T026: search_similar cluster_id filter (schema only) ------------


def test_t026_search_similar_params_accepts_cluster_id() -> None:
    from mememo.tools.schemas import SearchSimilarParams

    p = SearchSimilarParams(query="q", cluster_id=3)
    assert p.cluster_id == 3
    # Default keeps backward-compat.
    p2 = SearchSimilarParams(query="q")
    assert p2.cluster_id is None
