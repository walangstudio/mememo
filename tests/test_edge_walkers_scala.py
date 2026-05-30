"""Scala edge taxonomy via tree-sitter (EDGE_WALKERS registry).

Covers walk_scala: IMPORTS, EXTENDS/IMPLEMENTS (extends_clause with/without with),
CALLS, USES (this.field), plus class/object/trait/method chunk metadata.
Skipped when the Scala grammar is absent.
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
pytest.importorskip("tree_sitter_scala")

from mememo.chunking.tree_sitter_chunker import TreeSitterChunker  # noqa: E402

SCALA_SAMPLE = """\
import scala.collection.mutable.ListBuffer
import java.util.{List, Map}

trait Drawable {
  def draw(): Unit
}

abstract class Shape(val color: String) {
  def area(): Double
}

class Circle(radius: Double, color: String) extends Shape(color) with Drawable {
  override def area(): Double = Math.PI * radius * radius
  def draw(): Unit = {
    println(area())
    this.color
  }
  private def helper(): Unit = {}
}

object MathUtils {
  def square(x: Double): Double = x * x
}

def topLevel(): Unit = {
  val c = Circle(5.0, "red")
  c.draw()
  MathUtils.square(3.0)
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


def test_scala_imports(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(SCALA_SAMPLE, "src/geo.scala", "scala")
    imports = [e.target_label for e in edges if e.edge_type == "IMPORTS"]
    assert any("ListBuffer" in t for t in imports)
    # namespace_selectors expanded
    assert any("List" in t for t in imports)
    assert any("Map" in t for t in imports)


def test_scala_extends(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(SCALA_SAMPLE, "src/geo.scala", "scala")
    extends = _by_type(edges).get("EXTENDS", [])
    assert any(e.target_label == "Shape" for e in extends)


def test_scala_implements_with(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(SCALA_SAMPLE, "src/geo.scala", "scala")
    impls = _by_type(edges).get("IMPLEMENTS", [])
    assert any(e.target_label == "Drawable" for e in impls)


def test_scala_calls(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(SCALA_SAMPLE, "src/geo.scala", "scala")
    calls = [e.target_label for e in edges if e.edge_type == "CALLS"]
    assert any("println" in t for t in calls)
    assert any("area" in t for t in calls)
    assert any("MathUtils.square" in t for t in calls)


def test_scala_this_member_uses(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(SCALA_SAMPLE, "src/geo.scala", "scala")
    uses = _by_type(edges).get("USES", [])
    assert any(e.target_label == "color" for e in uses)


def test_scala_class_chunks(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(SCALA_SAMPLE, "src/geo.scala", "scala")
    class_names = {c.class_name for c in chunks if c.chunk_type == "class"}
    assert {"Circle", "Shape", "Drawable", "MathUtils"} <= class_names


def test_scala_method_chunks_carry_parent(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(SCALA_SAMPLE, "src/geo.scala", "scala")
    methods = [c for c in chunks if c.chunk_type == "method"]
    assert methods
    circle_methods = [m for m in methods if m.parent_class == "Circle"]
    assert circle_methods
    fn_names = {m.function_name for m in circle_methods}
    assert {"area", "draw", "helper"} <= fn_names


def test_scala_top_level_function(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(SCALA_SAMPLE, "src/geo.scala", "scala")
    funcs = [c for c in chunks if c.chunk_type == "function"]
    assert any(f.function_name == "topLevel" for f in funcs)
