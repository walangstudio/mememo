"""Regression guard for the TreeSitterChunker.chunk() query path.

The edge tests only exercise the manual-walk (ts_edges) path. This file
covers LANGUAGE_QUERIES against the installed standalone grammars so query
patterns that go stale with a grammar bump (e.g. the Go method receiver
clause) fail here instead of silently at runtime.
"""

from __future__ import annotations

import sys
import types as _types

import pytest


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

pytest.importorskip("tree_sitter")

from mememo.chunking.tree_sitter_chunker import (  # noqa: E402
    LANGUAGE_QUERIES,
    TreeSitterChunker,
)

SAMPLES = {
    "typescript": ("a.ts", "class A { m() {} }\nfunction f() {}\ninterface I {}\n"),
    "javascript": ("a.js", "class A { m() {} }\nfunction f() {}\n"),
    "go": ("a.go", "package m\nfunc F() {}\nfunc (r T) M() {}\ntype T struct{}\n"),
    "rust": ("a.rs", "fn f() {}\nstruct S;\ntrait Tr {}\nimpl S {}\n"),
    "java": ("A.java", "class C { void m() {} }\ninterface I {}\n"),
    "c": ("a.c", "int f() { return 0; }\nstruct S { int x; };\n"),
    "cpp": ("a.cpp", "int f() { return 0; }\nclass C {};\nstruct S {};\n"),
    "csharp": ("A.cs", "class C { void M() {} }\ninterface I {}\n"),
}


@pytest.fixture(scope="module")
def chunker() -> TreeSitterChunker:
    return TreeSitterChunker()


@pytest.mark.parametrize("language", sorted(LANGUAGE_QUERIES))
def test_chunk_query_path_yields_definitions(chunker: TreeSitterChunker, language: str):
    pytest.importorskip(
        {
            "typescript": "tree_sitter_typescript",
            "javascript": "tree_sitter_javascript",
            "go": "tree_sitter_go",
            "rust": "tree_sitter_rust",
            "java": "tree_sitter_java",
            "c": "tree_sitter_c",
            "cpp": "tree_sitter_cpp",
            "csharp": "tree_sitter_c_sharp",
        }[language]
    )
    file_path, code = SAMPLES[language]
    chunks = chunker.chunk(code, file_path, language)
    assert chunks, f"{language}: chunk() returned no chunks"
    assert all(c.language == language for c in chunks)


def test_go_method_chunk_extracted(chunker: TreeSitterChunker):
    pytest.importorskip("tree_sitter_go")
    chunks = chunker.chunk(SAMPLES["go"][1], "a.go", "go")
    kinds = {c.chunk_type for c in chunks}
    assert "function" in kinds
    assert "method" in kinds
