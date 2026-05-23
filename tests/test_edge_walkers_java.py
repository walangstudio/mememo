"""Java edge taxonomy via tree-sitter (EDGE_WALKERS registry).

Companion to test_edge_walkers_rust.py — covers walk_java: IMPORTS,
EXTENDS, IMPLEMENTS, CALLS, USES, plus method/class chunking. Skipped
when the Java grammar isn't installed.
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
pytest.importorskip("tree_sitter_java")

from mememo.chunking.tree_sitter_chunker import TreeSitterChunker  # noqa: E402

JAVA_SAMPLE = """\
package com.example;
import java.util.List;
import java.util.*;

class Animal {}
interface Walks {}

public class Dog extends Animal implements Walks {
    int legs = 4;

    void bark() {
        helper();
        this.run();
        System.out.println(this.legs);
    }

    void run() {}
}

class Util {
    static void helper() {}
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


def test_java_imports(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(JAVA_SAMPLE, "src/Dog.java", "java")
    imports = [e.target_label for e in edges if e.edge_type == "IMPORTS"]
    assert "java.util.List" in imports
    assert "java.util.*" in imports  # wildcard preserved


def test_java_extends_and_implements(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(JAVA_SAMPLE, "src/Dog.java", "java")
    by = _by_type(edges)
    assert any(
        e.source_qualname.endswith("Dog") and e.target_label == "Animal" for e in by["EXTENDS"]
    )
    assert any(
        e.source_qualname.endswith("Dog") and e.target_label == "Walks" for e in by["IMPLEMENTS"]
    )


def test_java_calls_all_callee_shapes(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(JAVA_SAMPLE, "src/Dog.java", "java")
    calls = [e.target_label for e in edges if e.edge_type == "CALLS"]
    assert "helper" in calls  # bare
    assert "this.run" in calls  # this-qualified
    assert "System.out.println" in calls  # field-access chain


def test_java_this_field_uses(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(JAVA_SAMPLE, "src/Dog.java", "java")
    uses = [e.target_label for e in edges if e.edge_type == "USES"]
    assert "legs" in uses  # this.legs read in println(...)


def test_java_method_chunks_carry_parent(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(JAVA_SAMPLE, "src/Dog.java", "java")
    methods = [c for c in chunks if c.chunk_type == "method"]
    assert {m.function_name for m in methods} >= {"bark", "run", "helper"}
    bark = next(m for m in methods if m.function_name == "bark")
    assert bark.parent_class == "Dog"
    classes = {c.class_name for c in chunks if c.chunk_type == "class"}
    assert {"Dog", "Animal", "Walks", "Util"} <= classes
