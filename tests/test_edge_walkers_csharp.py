"""C# edge taxonomy via tree-sitter (EDGE_WALKERS registry).

Companion to test_edge_walkers_rust.py — covers walk_csharp: IMPORTS,
EXTENDS, CALLS, USES, plus method/class chunking. Skipped when the C#
grammar isn't installed.
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
pytest.importorskip("tree_sitter_c_sharp")

from mememo.chunking.tree_sitter_chunker import TreeSitterChunker  # noqa: E402

CSHARP_SAMPLE = """\
using System;
using System.Collections.Generic;
using Foo = System.Console;

namespace App {
    interface IWalks {}
    class Animal {}

    public class Dog : Animal, IWalks {
        public int Legs;
        public void Bark() {
            Helper();
            this.Run();
            Console.WriteLine("woof");
            var n = this.Legs;
        }
        public void Run() {}
    }

    static class Util {
        public static void Helper() {}
    }
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


def test_csharp_imports(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(CSHARP_SAMPLE, "src/Dog.cs", "csharp")
    imports = [e.target_label for e in edges if e.edge_type == "IMPORTS"]
    assert "System" in imports
    assert "System.Collections.Generic" in imports
    assert "System.Console" in imports  # aliased using resolves to its target


def test_csharp_extends_base_list(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(CSHARP_SAMPLE, "src/Dog.cs", "csharp")
    ext = [(e.source_qualname, e.target_label) for e in _by_type(edges).get("EXTENDS", [])]
    # C# grammar lists base class + interfaces together; both emit EXTENDS.
    assert any(s.endswith("Dog") and t == "Animal" for s, t in ext)
    assert any(s.endswith("Dog") and t == "IWalks" for s, t in ext)


def test_csharp_calls_all_callee_shapes(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(CSHARP_SAMPLE, "src/Dog.cs", "csharp")
    calls = [e.target_label for e in edges if e.edge_type == "CALLS"]
    assert "Helper" in calls  # bare
    assert "this.Run" in calls  # this-qualified
    assert "Console.WriteLine" in calls  # member access


def test_csharp_uses(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(CSHARP_SAMPLE, "src/Dog.cs", "csharp")
    uses = [e.target_label for e in edges if e.edge_type == "USES"]
    assert "Legs" in uses  # this.Legs read
    assert "Dog" in uses  # method binds to its class


def test_csharp_method_chunks_carry_parent(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(CSHARP_SAMPLE, "src/Dog.cs", "csharp")
    methods = [c for c in chunks if c.chunk_type == "method"]
    assert {m.function_name for m in methods} >= {"Bark", "Run", "Helper"}
    bark = next(m for m in methods if m.function_name == "Bark")
    assert bark.parent_class == "Dog"
    classes = {c.class_name for c in chunks if c.chunk_type == "class"}
    assert {"Dog", "Animal", "IWalks", "Util"} <= classes
