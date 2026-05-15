"""v0.6 batch 3 — Cypher subset parser, SQL translation, and tool round-trip."""

from __future__ import annotations

import asyncio
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

from mememo.core.cypher_parser import (  # noqa: E402
    UnsupportedCypherError,
    parse_cypher,
    translate_to_sql,
)
from mememo.core.storage_manager import StorageManager  # noqa: E402
from mememo.types import Relation  # noqa: E402

SHA = "a" * 40


# ---------- T035 parser positive cases -------------------------------------


def test_parses_basic_match_return() -> None:
    q = parse_cypher("MATCH (a)-[r:CALLS]->(b) RETURN a.id, b.id")
    assert q.pattern.rel_type == "CALLS"
    assert q.pattern.direction == "out"
    assert [p.prop for p in q.projections] == ["id", "id"]


def test_parses_where_with_regex_and_and() -> None:
    q = parse_cypher(
        "MATCH (a)-[r:CALLS]->(b) "
        'WHERE a.file_path =~ "mememo/core/.*" AND r.confidence = "EXTRACTED" '
        "RETURN b.id LIMIT 10"
    )
    assert len(q.predicates) == 2
    assert q.predicates[0].op == "=~"
    assert q.connectors == ["AND"]
    assert q.limit == 10


def test_parses_undirected_pattern() -> None:
    q = parse_cypher("MATCH (a)-[r:USES]-(b) RETURN a.id")
    assert q.pattern.direction == "undirected"


def test_parses_return_with_alias() -> None:
    q = parse_cypher("MATCH (a)-[r:IMPORTS]->(b) RETURN b.id AS target_id")
    assert q.projections[0].alias == "target_id"


# ---------- T035 parser rejects unsupported --------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "MERGE (a) RETURN a.id",
        "MATCH (a) WITH a RETURN a.id",
        "MATCH (a)-[r:CALLS*1..3]->(b) RETURN b.id",
        "CREATE (a)",
        "MATCH (a)-[r:CALLS]->(b) RETURN a.id ORDER BY a.id",
        "MATCH (a)-[r:CALLS]->(b) OPTIONAL MATCH (b)-[r2:USES]->(c) RETURN c.id",
    ],
)
def test_rejects_unsupported_constructs(query: str) -> None:
    with pytest.raises(UnsupportedCypherError):
        parse_cypher(query)


def test_rejects_query_without_match() -> None:
    with pytest.raises(UnsupportedCypherError):
        parse_cypher("RETURN 1")


def test_rejects_return_with_non_property() -> None:
    with pytest.raises(UnsupportedCypherError):
        parse_cypher("MATCH (a)-[r:CALLS]->(b) RETURN COUNT(a)")


def test_rejects_invalid_where_connector() -> None:
    with pytest.raises(UnsupportedCypherError):
        parse_cypher('MATCH (a)-[r:CALLS]->(b) WHERE a.id = "x" XOR r.id = "y" RETURN a.id')


def test_rejects_unknown_property() -> None:
    q = parse_cypher('MATCH (a)-[r:CALLS]->(b) WHERE a.nonexistent = "x" RETURN a.id')
    with pytest.raises(UnsupportedCypherError):
        translate_to_sql(q)


# ---------- T035 SQL translation -------------------------------------------


def test_sql_translation_basic() -> None:
    q = parse_cypher("MATCH (a)-[r:CALLS]->(b) RETURN a.id, b.id LIMIT 5")
    sql, params, _ = translate_to_sql(q)
    assert "FROM relations r" in sql
    assert "INNER JOIN memories src" in sql
    assert "LEFT JOIN memories tgt" in sql
    assert "r.type = ?" in sql
    assert "LIMIT ?" in sql
    assert params == ["CALLS", 5]


def test_sql_translation_with_regex() -> None:
    q = parse_cypher('MATCH (a)-[r:IMPORTS]->(b) WHERE a.file_path =~ "mememo/.*" RETURN b.id')
    sql, params, _ = translate_to_sql(q)
    assert "REGEXP ?" in sql
    assert params == ["IMPORTS", "mememo/.*"]


# ---------- T035 end-to-end tool round-trip --------------------------------


class _StubMM:
    def __init__(self, storage: StorageManager) -> None:
        self.storage_manager = storage


def _seed(storage: StorageManager) -> None:
    # Memories: m1 in mememo/core, m2 in mememo/tools, m3 in benchmarks/
    for mid, fpath in [
        ("m1", "mememo/core/storage.py"),
        ("m2", "mememo/tools/foo.py"),
        ("m3", "benchmarks/bar.py"),
    ]:
        storage.conn.execute(
            "INSERT INTO memories (id, repo_id, branch_name, content_type, "
            " file_path, checksum, content_ref, token_count, created_at, updated_at) "
            f"VALUES ('{mid}', 'r', 'main', 'code_snippet', "
            f"'{fpath}', 'k', 'u', 1, 1, 1)"
        )
    storage.conn.commit()
    # Edges: m1 -> m2 (CALLS, EXTRACTED); m2 -> m3 (CALLS, INFERRED)
    storage.insert_relations(
        [
            Relation(
                id="r1",
                repo_id="r",
                branch="main",
                source_memory_id="m1",
                target_memory_id="m2",
                type="CALLS",
                confidence="EXTRACTED",
                created_at_sha=SHA,
            ),
            Relation(
                id="r2",
                repo_id="r",
                branch="main",
                source_memory_id="m2",
                target_memory_id="m3",
                type="CALLS",
                confidence="INFERRED",
                created_at_sha=SHA,
            ),
        ]
    )


def test_tool_returns_filtered_rows(tmp_path: Path) -> None:
    from mememo.tools.cypher_query import CypherQueryParams, cypher_query

    storage = StorageManager(base_dir=tmp_path / "store")
    _seed(storage)
    mm = _StubMM(storage)
    resp = asyncio.run(
        cypher_query(
            CypherQueryParams(
                query=(
                    "MATCH (a)-[r:CALLS]->(b) "
                    'WHERE a.file_path =~ "mememo/core/.*" '
                    "RETURN b.id, r.confidence LIMIT 10"
                )
            ),
            mm,
        )
    )
    assert resp.success
    assert resp.row_count == 1
    assert resp.rows[0]["b.id"] == "m2"
    assert resp.rows[0]["r.confidence"] == "EXTRACTED"


def test_tool_returns_structured_error_for_unsupported(tmp_path: Path) -> None:
    from mememo.tools.cypher_query import CypherQueryParams, cypher_query

    storage = StorageManager(base_dir=tmp_path / "store")
    mm = _StubMM(storage)
    resp = asyncio.run(
        cypher_query(
            CypherQueryParams(query="MERGE (a) RETURN a.id"),
            mm,
        )
    )
    assert not resp.success
    assert resp.error_kind == "unsupported"
    assert resp.rows == []


def test_server_registers_cypher_tool() -> None:
    import importlib

    srv = importlib.import_module("mememo.server")
    assert hasattr(srv, "cypher_query")
