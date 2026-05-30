"""Swift edge taxonomy via tree-sitter (EDGE_WALKERS registry).

Covers walk_swift: IMPORTS, EXTENDS/IMPLEMENTS (inheritance_specifier),
CALLS, USES (self.member), plus class/method chunk metadata.
Skipped when the Swift grammar is absent.
"""

from __future__ import annotations

import sys
import types as _types

import pytest


def _stub_module(name: str, **attrs: object) -> None:
    if name in sys.modules:
        return
    try:
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
pytest.importorskip("tree_sitter_swift")

from mememo.chunking.tree_sitter_chunker import TreeSitterChunker  # noqa: E402

SWIFT_SAMPLE = """\
import Foundation
import UIKit

protocol Drawable {
    func draw() -> Void
}

class Shape: NSObject, Drawable {
    var color: String = "red"

    func draw() -> Void {
        print(color)
        self.color
    }
}

struct Circle: Drawable {
    let radius: Double

    func draw() -> Void {
        let area = computeArea()
        print(area)
    }

    private func computeArea() -> Double {
        return Double.pi * radius * radius
    }
}

func topLevel() {
    let c = Circle(radius: 5.0)
    c.draw()
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


def test_swift_imports(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(SWIFT_SAMPLE, "Sources/geo.swift", "swift")
    imports = [e.target_label for e in edges if e.edge_type == "IMPORTS"]
    assert "Foundation" in imports
    assert "UIKit" in imports


def test_swift_extends_first_base(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(SWIFT_SAMPLE, "Sources/geo.swift", "swift")
    extends = _by_type(edges).get("EXTENDS", [])
    assert any(e.target_label == "NSObject" for e in extends)


def test_swift_implements_protocol(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(SWIFT_SAMPLE, "Sources/geo.swift", "swift")
    impls = _by_type(edges).get("IMPLEMENTS", [])
    assert any(e.target_label == "Drawable" for e in impls)


def test_swift_calls(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(SWIFT_SAMPLE, "Sources/geo.swift", "swift")
    calls = [e.target_label for e in edges if e.edge_type == "CALLS"]
    assert any("print" in t for t in calls)
    assert any("computeArea" in t for t in calls)


def test_swift_self_member_uses(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(SWIFT_SAMPLE, "Sources/geo.swift", "swift")
    uses = _by_type(edges).get("USES", [])
    assert any(e.target_label == "color" for e in uses)


def test_swift_class_chunks(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(SWIFT_SAMPLE, "Sources/geo.swift", "swift")
    class_names = {c.class_name for c in chunks if c.chunk_type == "class"}
    assert {"Shape", "Circle", "Drawable"} <= class_names


def test_swift_method_chunks_carry_parent(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(SWIFT_SAMPLE, "Sources/geo.swift", "swift")
    methods = [c for c in chunks if c.chunk_type == "method"]
    assert methods
    shape_methods = [m for m in methods if m.parent_class == "Shape"]
    assert shape_methods
    assert any(m.function_name == "draw" for m in shape_methods)


def test_swift_top_level_function(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(SWIFT_SAMPLE, "Sources/geo.swift", "swift")
    funcs = [c for c in chunks if c.chunk_type == "function"]
    assert any(f.function_name == "topLevel" for f in funcs)
