"""PHP edge taxonomy via tree-sitter (EDGE_WALKERS registry).

Covers walk_php: IMPORTS (use), EXTENDS (base_clause), IMPLEMENTS
(class_interface_clause), CALLS, plus class/method chunk metadata.
Skipped when the PHP grammar is absent.
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
pytest.importorskip("tree_sitter_php")

from mememo.chunking.tree_sitter_chunker import TreeSitterChunker  # noqa: E402

PHP_SAMPLE = r"""<?php
namespace App\Controllers;

use App\Models\User;
use App\Services\AuthService;

interface Authenticatable {
    public function authenticate(): bool;
}

abstract class BaseController {
    protected function render(string $view): string {
        return $view;
    }
}

class UserController extends BaseController implements Authenticatable {
    public function authenticate(): bool {
        $result = AuthService::verify();
        helper_func();
        return $result;
    }

    public function index(): void {
        $user = User::find(1);
    }
}

function helper_func(): void {
    echo "helper";
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


def test_php_imports(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(PHP_SAMPLE, "src/UserController.php", "php")
    imports = [e.target_label for e in edges if e.edge_type == "IMPORTS"]
    assert any("User" in t for t in imports)
    assert any("AuthService" in t for t in imports)


def test_php_extends(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(PHP_SAMPLE, "src/UserController.php", "php")
    extends = _by_type(edges).get("EXTENDS", [])
    assert any(e.target_label == "BaseController" for e in extends)


def test_php_implements(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(PHP_SAMPLE, "src/UserController.php", "php")
    impls = _by_type(edges).get("IMPLEMENTS", [])
    assert any(e.target_label == "Authenticatable" for e in impls)


def test_php_calls(chunker: TreeSitterChunker) -> None:
    _, edges = chunker.chunk_with_edges(PHP_SAMPLE, "src/UserController.php", "php")
    calls = [e.target_label for e in edges if e.edge_type == "CALLS"]
    assert any("helper_func" in t for t in calls)
    assert any("verify" in t for t in calls)


def test_php_class_chunks(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(PHP_SAMPLE, "src/UserController.php", "php")
    class_names = {c.class_name for c in chunks if c.chunk_type == "class"}
    assert {"UserController", "BaseController", "Authenticatable"} <= class_names


def test_php_method_chunks_carry_parent(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(PHP_SAMPLE, "src/UserController.php", "php")
    methods = [c for c in chunks if c.chunk_type == "method"]
    assert methods
    controller_methods = [m for m in methods if m.parent_class == "UserController"]
    assert controller_methods
    fn_names = {m.function_name for m in controller_methods}
    assert {"authenticate", "index"} <= fn_names


def test_php_top_level_function(chunker: TreeSitterChunker) -> None:
    chunks, _ = chunker.chunk_with_edges(PHP_SAMPLE, "src/UserController.php", "php")
    funcs = [c for c in chunks if c.chunk_type == "function"]
    assert any(f.function_name == "helper_func" for f in funcs)


# Regression: grouped use `use App\Http\{HomeController, UserController}` lost
# the common namespace prefix — _gather_use_targets returned bare names.
# Simple `use App\Foo` form must keep working too.
def test_php_grouped_use_preserves_namespace_prefix(chunker: TreeSitterChunker) -> None:
    code = r"""<?php
use App\Http\{HomeController, UserController};
use App\Foo;

class Stub {}
"""
    _, edges = chunker.chunk_with_edges(code, "src/stub.php", "php")
    imports = [e.target_label for e in edges if e.edge_type == "IMPORTS"]
    # Grouped form must include the full path with prefix
    assert any(
        "HomeController" in t and "App" in t for t in imports
    ), f"Grouped-use prefix missing; imports: {imports}"
    assert any(
        "UserController" in t and "App" in t for t in imports
    ), f"Grouped-use prefix missing; imports: {imports}"
    # Simple form must still work
    assert any("App" in t and "Foo" in t for t in imports), f"Simple use broken; imports: {imports}"
