"""Kotlin edge taxonomy via tree-sitter (EDGE_WALKERS registry).

Covers walk_kotlin: IMPORTS, EXTENDS, IMPLEMENTS, CALLS, plus
class/method chunk metadata. Skipped when the Kotlin grammar is absent.
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
pytest.importorskip("tree_sitter_kotlin")

from mememo.chunking.tree_sitter_chunker import TreeSitterChunker  # noqa: E402

KOTLIN_SAMPLE = """\
import java.util.List
import kotlin.collections.Map

interface Shape {
    fun area(): Double
}

abstract class Animal(val name: String) : Comparable<Animal> {
    abstract fun speak(): String
}

class Dog(name: String) : Animal(name), Shape {
    override fun speak(): String = "Woof"
    override fun area(): Double = 0.0
    fun fetch(item: String) {
        println(item)
        speak()
    }
}

object Singleton {
    fun getInstance(): Singleton = this
}

fun topLevel(x: Int): Int {
    val d = Dog("Rex")
    return d.speak().length
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


def test_kotlin_imports(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(KOTLIN_SAMPLE, "src/animals.kt", "kotlin")
    imports = [e.target_label for e in edges if e.edge_type == "IMPORTS"]
    assert "java.util.List" in imports
    assert "kotlin.collections.Map" in imports


def test_kotlin_extends(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(KOTLIN_SAMPLE, "src/animals.kt", "kotlin")
    extends = _by_type(edges).get("EXTENDS", [])
    # Dog's first base is Animal (constructor_invocation)
    assert any(e.target_label == "Animal" for e in extends)
    # Animal's first base is Comparable
    assert any(e.target_label == "Comparable" for e in extends)


def test_kotlin_implements(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(KOTLIN_SAMPLE, "src/animals.kt", "kotlin")
    impls = _by_type(edges).get("IMPLEMENTS", [])
    # Dog's second base (Shape) is IMPLEMENTS
    assert any(e.target_label == "Shape" for e in impls)


def test_kotlin_calls(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(KOTLIN_SAMPLE, "src/animals.kt", "kotlin")
    calls = [e.target_label for e in edges if e.edge_type == "CALLS"]
    assert "println" in calls
    assert "speak" in calls


def test_kotlin_class_chunks(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(KOTLIN_SAMPLE, "src/animals.kt", "kotlin")
    class_names = {c.class_name for c in chunks if c.chunk_type == "class"}
    assert {"Dog", "Animal", "Shape", "Singleton"} <= class_names


def test_kotlin_method_chunks_carry_parent(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(KOTLIN_SAMPLE, "src/animals.kt", "kotlin")
    methods = [c for c in chunks if c.chunk_type == "method"]
    assert methods
    dog_methods = [m for m in methods if m.parent_class == "Dog"]
    assert dog_methods
    fn_names = {m.function_name for m in dog_methods}
    assert {"speak", "area", "fetch"} <= fn_names


def test_kotlin_top_level_function(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(KOTLIN_SAMPLE, "src/animals.kt", "kotlin")
    funcs = [c for c in chunks if c.chunk_type == "function"]
    assert any(f.function_name == "topLevel" for f in funcs)
