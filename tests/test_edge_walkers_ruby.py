"""Ruby edge taxonomy via tree-sitter (EDGE_WALKERS registry).

Covers walk_ruby: IMPORTS (require), EXTENDS (superclass), IMPLEMENTS (include),
CALLS, plus class/method chunk metadata. Skipped when the Ruby grammar is absent.
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
pytest.importorskip("tree_sitter_ruby")

from mememo.chunking.tree_sitter_chunker import TreeSitterChunker  # noqa: E402

RUBY_SAMPLE = """\
require "json"
require_relative "./base"

module Animals
  class Dog < Animal
    include Comparable
    include Serializable

    def initialize(name)
      @name = name
    end

    def speak
      puts @name
      greet()
    end

    def self.create(name)
      new(name)
    end
  end
end

def top_level_func
  d = Dog.new("Rex")
  d.speak
end
"""


@pytest.fixture(scope="module")
def chunker() -> TreeSitterChunker:
    return TreeSitterChunker()


def _by_type(edges) -> dict[str, list]:
    out: dict[str, list] = {}
    for e in edges:
        out.setdefault(e.edge_type, []).append(e)
    return out


def test_ruby_imports(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(RUBY_SAMPLE, "lib/dog.rb", "ruby")
    imports = [e.target_label for e in edges if e.edge_type == "IMPORTS"]
    assert any("json" in t for t in imports)
    assert any("base" in t for t in imports)


def test_ruby_extends(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(RUBY_SAMPLE, "lib/dog.rb", "ruby")
    extends = _by_type(edges).get("EXTENDS", [])
    assert any(e.target_label == "Animal" for e in extends)


def test_ruby_implements_mixins(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(RUBY_SAMPLE, "lib/dog.rb", "ruby")
    impls = _by_type(edges).get("IMPLEMENTS", [])
    targets = [e.target_label for e in impls]
    assert "Comparable" in targets
    assert "Serializable" in targets


def test_ruby_calls(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(RUBY_SAMPLE, "lib/dog.rb", "ruby")
    calls = [e.target_label for e in edges if e.edge_type == "CALLS"]
    assert any("puts" in t for t in calls)
    assert any("greet" in t for t in calls)


def test_ruby_class_chunks(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(RUBY_SAMPLE, "lib/dog.rb", "ruby")
    class_names = {c.class_name for c in chunks if c.chunk_type == "class"}
    assert "Dog" in class_names
    assert "Animals" in class_names


def test_ruby_method_chunks_carry_parent(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(RUBY_SAMPLE, "lib/dog.rb", "ruby")
    methods = [c for c in chunks if c.chunk_type == "method"]
    assert methods
    dog_methods = [m for m in methods if m.parent_class == "Dog"]
    assert dog_methods
    fn_names = {m.function_name for m in dog_methods}
    assert {"initialize", "speak", "create"} <= fn_names


def test_ruby_top_level_function(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(RUBY_SAMPLE, "lib/dog.rb", "ruby")
    funcs = [c for c in chunks if c.chunk_type == "function"]
    assert any(f.function_name == "top_level_func" for f in funcs)


# Regression: IMPLEMENTS source_qualname was doubled ("mod.Dog.Dog") because
# cur() already contains the class segment after the class push.  Fix: use
# cur() directly instead of f"{cur()}.{enclosing_class()}".
def test_ruby_implements_source_qualname_not_doubled(chunker: TreeSitterChunker) -> None:
    code = """\
module Animals
  class Dog
    include Walkable
  end
end
"""
    _, edges = chunker.chunk_with_edges(code, "lib/dog.rb", "ruby")
    impls = [e for e in edges if e.edge_type == "IMPLEMENTS" and e.target_label == "Walkable"]
    assert impls, "Expected an IMPLEMENTS edge for Walkable"
    src = impls[0].source_qualname
    # Must match the registered class chunk qualname: module.Animals.Dog
    # NOT the doubled form: module.Animals.Dog.Dog
    assert (
        src == "lib.dog.Animals.Dog"
    ), f"source_qualname doubled: got {src!r}, expected 'lib.dog.Animals.Dog'"
