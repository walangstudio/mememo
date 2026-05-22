"""v0.5 batch 3 — TypeScript / JavaScript / Go edge taxonomy via tree-sitter.

Covers T016 (TypeScript), T017 (JavaScript), T018 (Go). Skipped when
the tree-sitter grammars aren't installed.
"""

from __future__ import annotations

import sys
import types as _types

import pytest


# Same heavy-dep stubbing pattern as the rest of the suite so this file can run
# in any env where tree-sitter IS available but sentence-transformers / faiss
# may not be.
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


# Tree-sitter is the actual subject under test here — skip the whole module
# when it isn't installed.
pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_go")

from mememo.chunking.tree_sitter_chunker import TreeSitterChunker  # noqa: E402


@pytest.fixture(scope="module")
def chunker() -> TreeSitterChunker:
    return TreeSitterChunker()


# ---------- T016: TypeScript ------------------------------------------------


TS_SAMPLE = """\
import { Logger } from "./logger";
import express from "express";

@Controller
class UserService extends BaseService implements IService {
    log: Logger;

    fetch(id: string) {
        this.log.info("fetching");
        return baz(id);
    }
}
"""


def test_t016_typescript_imports(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(TS_SAMPLE, "src/user.ts", "typescript")
    imports = [e for e in edges if e.edge_type == "IMPORTS"]
    assert any(e.target_label == "./logger.Logger" for e in imports)
    assert any(e.target_label == "express.express" for e in imports)


def test_t016_typescript_extends_implements(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(TS_SAMPLE, "src/user.ts", "typescript")
    extends = [e for e in edges if e.edge_type == "EXTENDS"]
    implements = [e for e in edges if e.edge_type == "IMPLEMENTS"]
    assert any(e.target_label == "BaseService" for e in extends)
    assert any(e.target_label == "IService" for e in implements)


def test_t016_typescript_calls_and_uses(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(TS_SAMPLE, "src/user.ts", "typescript")
    calls = [e for e in edges if e.edge_type == "CALLS"]
    uses = [e for e in edges if e.edge_type == "USES"]
    assert any(e.target_label == "baz" for e in calls)
    # this.log.info → CALLS on `this.log.info` AND USES on `log`
    assert any(e.target_label == "log" for e in uses)


def test_t016_typescript_method_qualname_includes_class(
    chunker: TreeSitterChunker,
) -> None:
    chunks, _ = chunker.chunk_with_edges(TS_SAMPLE, "src/user.ts", "typescript")
    methods = [c for c in chunks if c.chunk_type == "method"]
    assert methods
    assert all(m.parent_class == "UserService" for m in methods)


# ---------- T017: JavaScript ------------------------------------------------


JS_SAMPLE = """\
import { format } from "./fmt";

class Greeter extends Base {
    greet(name) {
        return format(this.prefix + name);
    }
}
"""


def test_t017_javascript_imports_extends_calls(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(JS_SAMPLE, "src/greeter.js", "javascript")
    by_type: dict[str, list] = {}
    for e in edges:
        by_type.setdefault(e.edge_type, []).append(e)
    assert any(e.target_label == "./fmt.format" for e in by_type.get("IMPORTS", []))
    assert any(e.target_label == "Base" for e in by_type.get("EXTENDS", []))
    assert any(e.target_label == "format" for e in by_type.get("CALLS", []))
    assert any(e.target_label == "prefix" for e in by_type.get("USES", []))


# ---------- T018: Go --------------------------------------------------------


GO_SAMPLE = """\
package main

import (
    "fmt"
    "github.com/foo/bar"
)

type User struct {
    Name string
}

func (u *User) Greet() {
    fmt.Println("hi", u.Name)
    bar.Helper()
}

func main() {
    u := User{Name: "x"}
    u.Greet()
}
"""


def test_t018_go_imports(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(GO_SAMPLE, "main.go", "go")
    imports = [e for e in edges if e.edge_type == "IMPORTS"]
    targets = {e.target_label for e in imports}
    assert "fmt" in targets
    assert "github.com/foo/bar" in targets


def test_t018_go_calls(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(GO_SAMPLE, "main.go", "go")
    calls = {e.target_label for e in edges if e.edge_type == "CALLS"}
    # Selector chains preserved.
    assert "fmt.Println" in calls
    assert "bar.Helper" in calls
    assert "u.Greet" in calls


def test_t018_go_method_receiver_emits_uses(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(GO_SAMPLE, "main.go", "go")
    uses = [e for e in edges if e.edge_type == "USES"]
    assert any(e.target_label == "User" for e in uses)


def test_t018_go_method_chunk_carries_receiver_as_parent_class(
    chunker: TreeSitterChunker,
) -> None:
    chunks, _ = chunker.chunk_with_edges(GO_SAMPLE, "main.go", "go")
    methods = [c for c in chunks if c.chunk_type == "method"]
    assert methods
    assert methods[0].parent_class == "User"


# ---------- Fallback for languages without an extractor ---------------------


def test_other_language_returns_chunks_no_edges(chunker: TreeSitterChunker) -> None:
    """Java is supported by the chunker but not by the v0.5 edge extractor —
    chunk_with_edges should return chunks and an empty edge list."""
    java_sample = "class Foo {\n  void bar() {}\n}\n"
    chunks, edges = chunker.chunk_with_edges(java_sample, "Foo.java", "java")
    assert chunks
    assert edges == []
