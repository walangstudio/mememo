"""Rust edge taxonomy via tree-sitter (EDGE_WALKERS registry).

Companion to test_v05_ts_js_go_edges.py — covers walk_rust: IMPORTS,
CALLS, IMPLEMENTS, USES, plus method/class chunking. Skipped when the
Rust grammar isn't installed.
"""

from __future__ import annotations

import sys
import types as _types

import pytest


def _stub_module(name: str, **attrs: object) -> None:
    if name in sys.modules:
        return
    try:  # never shadow a real, installed module (would leak into other tests)
        __import__(name)
        return
    except ImportError:
        pass
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

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_rust")

from mememo.chunking.tree_sitter_chunker import TreeSitterChunker  # noqa: E402

RUST_SAMPLE = """\
use std::collections::HashMap;
use crate::foo::Bar;

struct Point { x: i32 }

trait Shape { fn area(&self) -> f64; }

impl Shape for Point {
    fn area(&self) -> f64 {
        helper();
        self.x as f64
    }
}

impl Point {
    fn new() -> Self { Point { x: 0 } }
}

fn helper() {}

fn main() {
    let pt = Point::new();
    pt.area();
    helper();
}
"""


@pytest.fixture(scope="module")
def chunker() -> TreeSitterChunker:
    return TreeSitterChunker()


def _by_type(edges) -> dict[str, list]:
    out: dict[str, list] = {}
    for e in edges:
        out.setdefault(e.edge_type, []).append(e)
    return out


def test_rust_imports(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(RUST_SAMPLE, "src/geo.rs", "rust")
    imports = [e.target_label for e in edges if e.edge_type == "IMPORTS"]
    assert "std::collections::HashMap" in imports
    assert "crate::foo::Bar" in imports


def test_rust_implements(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(RUST_SAMPLE, "src/geo.rs", "rust")
    impls = _by_type(edges).get("IMPLEMENTS", [])
    assert any(e.source_qualname == "Point" and e.target_label == "Shape" for e in impls)


def test_rust_calls_all_callee_shapes(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(RUST_SAMPLE, "src/geo.rs", "rust")
    calls = [e.target_label for e in edges if e.edge_type == "CALLS"]
    # bare identifier, scoped (::), and field (.) callees
    assert "helper" in calls
    assert "Point::new" in calls
    assert "pt.area" in calls


def test_rust_method_uses_impl_type(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(RUST_SAMPLE, "src/geo.rs", "rust")
    uses = _by_type(edges).get("USES", [])
    assert any(e.target_label == "Point" for e in uses)


def test_rust_method_chunks_carry_parent(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(RUST_SAMPLE, "src/geo.rs", "rust")
    methods = [c for c in chunks if c.chunk_type == "method"]
    assert methods
    assert all(m.parent_class == "Point" for m in methods)
    classes = {c.class_name for c in chunks if c.chunk_type == "class"}
    assert {"Point", "Shape"} <= classes
