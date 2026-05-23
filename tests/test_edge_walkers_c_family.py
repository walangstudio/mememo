"""C / C++ edge taxonomy via tree-sitter (EDGE_WALKERS registry).

Companion to test_edge_walkers_rust.py — covers walk_c_family for both
languages: IMPORTS, CALLS (C), plus EXTENDS / USES (C++). Skipped when
the relevant grammar isn't installed.
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

from mememo.chunking.tree_sitter_chunker import TreeSitterChunker  # noqa: E402

C_SAMPLE = """\
#include <stdio.h>
#include "local.h"

struct Point { int x; };

void helper(void) {}

int main(void) {
    helper();
    printf("hi");
    return 0;
}
"""

CPP_SAMPLE = """\
#include <vector>
#include "shape.h"

class Animal {};
class Walks {};

class Dog : public Animal, public Walks {
public:
    int legs;
    void bark() {
        helper();
        this->run();
        int n = this->legs;
        std::sort(legs);
    }
    void run() {}
};

void helper() {}
"""


@pytest.fixture(scope="module")
def chunker() -> TreeSitterChunker:
    return TreeSitterChunker()


def _by_type(edges) -> dict[str, list]:
    out: dict[str, list] = {}
    for e in edges:
        out.setdefault(e.edge_type, []).append(e)
    return out


def test_c_imports_and_calls(chunker: TreeSitterChunker) -> None:
    pytest.importorskip("tree_sitter_c")
    _, edges = chunker.chunk_with_edges(C_SAMPLE, "src/m.c", "c")
    by = _by_type(edges)
    imports = [e.target_label for e in by.get("IMPORTS", [])]
    assert "stdio.h" in imports  # <> stripped
    assert "local.h" in imports  # "" stripped
    calls = [e.target_label for e in by.get("CALLS", [])]
    assert "helper" in calls
    assert "printf" in calls


def test_c_struct_and_function_chunks(chunker: TreeSitterChunker) -> None:
    pytest.importorskip("tree_sitter_c")
    chunks, _ = chunker.chunk_with_edges(C_SAMPLE, "src/m.c", "c")
    assert "Point" in {c.class_name for c in chunks if c.chunk_type == "class"}
    funcs = {c.function_name for c in chunks if c.chunk_type == "function"}
    assert {"helper", "main"} <= funcs


def test_cpp_includes_and_extends(chunker: TreeSitterChunker) -> None:
    pytest.importorskip("tree_sitter_cpp")
    _, edges = chunker.chunk_with_edges(CPP_SAMPLE, "src/dog.cpp", "cpp")
    by = _by_type(edges)
    assert "vector" in [e.target_label for e in by.get("IMPORTS", [])]
    ext = [(e.source_qualname, e.target_label) for e in by.get("EXTENDS", [])]
    assert any(s.endswith("Dog") and t == "Animal" for s, t in ext)
    assert any(s.endswith("Dog") and t == "Walks" for s, t in ext)  # multiple bases


def test_cpp_calls_and_uses(chunker: TreeSitterChunker) -> None:
    pytest.importorskip("tree_sitter_cpp")
    _, edges = chunker.chunk_with_edges(CPP_SAMPLE, "src/dog.cpp", "cpp")
    by = _by_type(edges)
    calls = [e.target_label for e in by.get("CALLS", [])]
    assert "helper" in calls  # bare
    assert "this.run" in calls  # this-> qualified
    assert "std::sort" in calls  # qualified_identifier
    uses = [e.target_label for e in by.get("USES", [])]
    assert "legs" in uses  # this->legs field read
    assert "Dog" in uses  # method binds to its class


def test_cpp_method_chunks_carry_parent(chunker: TreeSitterChunker) -> None:
    pytest.importorskip("tree_sitter_cpp")
    chunks, _ = chunker.chunk_with_edges(CPP_SAMPLE, "src/dog.cpp", "cpp")
    methods = [c for c in chunks if c.chunk_type == "method"]
    assert {m.function_name for m in methods} >= {"bark", "run"}
    assert all(m.parent_class == "Dog" for m in methods)
