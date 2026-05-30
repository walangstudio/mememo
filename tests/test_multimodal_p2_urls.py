"""Tests for multimodal P2 — URL extraction, reference type, docstring REFERENCES edges.

Covers:
  - url_extract.scan_urls / normalize_url
  - python_ast_chunker REFERENCES edges from docstrings
  - capture URL extraction -> reference memories
  - types: "reference" in MemoryContentType args and PERSISTENT_MEMORY_TYPES
"""

from __future__ import annotations

import asyncio
import sys
import types as _types
from typing import get_args
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Stub heavy deps before any mememo import
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Now safe to import
# ---------------------------------------------------------------------------


from mememo.chunking.python_ast_chunker import PythonASTChunker  # noqa: E402
from mememo.chunking.url_extract import normalize_url, scan_urls  # noqa: E402
from mememo.tools.capture import capture  # noqa: E402
from mememo.tools.schemas import CaptureParams  # noqa: E402
from mememo.types.memory import PERSISTENT_MEMORY_TYPES, MemoryContentType  # noqa: E402

# ===========================================================================
# url_extract
# ===========================================================================


class TestScanUrls:
    def test_finds_http_and_https(self):
        text = "See http://example.com and https://example.org for info."
        urls = scan_urls(text)
        assert "http://example.com" in urls
        assert "https://example.org" in urls

    def test_strips_trailing_period(self):
        urls = scan_urls("Visit https://example.com/path.")
        assert urls == ["https://example.com/path"]

    def test_strips_trailing_comma(self):
        urls = scan_urls("See https://example.com/a, and continue.")
        assert "https://example.com/a" in urls

    def test_strips_trailing_paren(self):
        urls = scan_urls("(https://example.com/x)")
        assert "https://example.com/x" in urls

    def test_strips_trailing_semicolon(self):
        urls = scan_urls("End https://example.com/b; done.")
        assert "https://example.com/b" in urls

    def test_deduplicates(self):
        text = "https://example.com/x and again https://example.com/x ."
        urls = scan_urls(text)
        assert urls.count("https://example.com/x") == 1

    def test_empty_text_returns_empty(self):
        assert scan_urls("") == []

    def test_no_urls_returns_empty(self):
        assert scan_urls("just plain text, no links") == []

    def test_preserves_query_string(self):
        url = "https://example.com/search?q=foo&bar=baz"
        assert scan_urls(f"See {url} now.") == [url]

    def test_other_scheme_matched(self):
        urls = scan_urls("ftp://files.example.com/thing.zip here.")
        assert "ftp://files.example.com/thing.zip" in urls

    def test_order_preserved(self):
        text = "First https://alpha.com then https://beta.com ."
        urls = scan_urls(text)
        assert urls.index("https://alpha.com") < urls.index("https://beta.com")


class TestNormalizeUrl:
    def test_lowercases_scheme(self):
        assert normalize_url("HTTPS://Example.COM/path").startswith("https://")

    def test_lowercases_host(self):
        norm = normalize_url("https://EXAMPLE.COM/path")
        assert "example.com" in norm

    def test_strips_trailing_slash(self):
        assert normalize_url("https://example.com/") == "https://example.com"

    def test_no_trailing_slash_unchanged(self):
        assert normalize_url("https://example.com/path") == "https://example.com/path"

    def test_dedup_with_trailing_slash(self):
        a = normalize_url("https://example.com")
        b = normalize_url("https://example.com/")
        assert a == b

    def test_no_scheme_returns_lowered(self):
        result = normalize_url("not-a-url")
        assert isinstance(result, str)


# ===========================================================================
# python_ast_chunker — REFERENCES edges from docstrings
# ===========================================================================


class TestPythonDocstringReferences:
    def test_function_docstring_url_emits_references_edge(self):
        code = '''\
def fetch_data():
    """Fetch data from https://api.example.com/v1/data."""
    pass
'''
        chunker = PythonASTChunker()
        chunks, edges = chunker.chunk_with_edges(code, "mymod.py")
        refs = [e for e in edges if e.edge_type == "REFERENCES"]
        assert refs, "expected a REFERENCES edge for the URL in the docstring"
        assert any(e.target_label == "https://api.example.com/v1/data" for e in refs)

    def test_references_edge_confidence_is_inferred(self):
        code = '''\
def thing():
    """See https://docs.example.com for usage."""
    pass
'''
        _, edges = PythonASTChunker().chunk_with_edges(code, "x.py")
        refs = [e for e in edges if e.edge_type == "REFERENCES"]
        assert all(e.confidence == "INFERRED" for e in refs)

    def test_references_edge_source_qualname_matches_function(self):
        code = '''\
def my_func():
    """See https://example.com for info."""
    pass
'''
        _, edges = PythonASTChunker().chunk_with_edges(code, "mymod/utils.py")
        refs = [e for e in edges if e.edge_type == "REFERENCES"]
        assert refs
        assert any("my_func" in e.source_qualname for e in refs)

    def test_class_docstring_url_emits_references_edge(self):
        code = '''\
class MyClient:
    """HTTP client. Docs at https://client.example.com/docs."""
    pass
'''
        _, edges = PythonASTChunker().chunk_with_edges(code, "client.py")
        refs = [e for e in edges if e.edge_type == "REFERENCES"]
        assert any(e.target_label == "https://client.example.com/docs" for e in refs)

    def test_no_docstring_emits_no_references(self):
        code = "def no_doc():\n    pass\n"
        _, edges = PythonASTChunker().chunk_with_edges(code, "x.py")
        refs = [e for e in edges if e.edge_type == "REFERENCES"]
        assert refs == []

    def test_duplicate_url_in_docstring_deduped(self):
        code = '''\
def thing():
    """See https://example.com and also https://example.com again."""
    pass
'''
        _, edges = PythonASTChunker().chunk_with_edges(code, "x.py")
        refs = [e for e in edges if e.edge_type == "REFERENCES"]
        url_labels = [e.target_label for e in refs]
        assert url_labels.count("https://example.com") == 1

    def test_multiple_urls_in_docstring(self):
        code = '''\
def thing():
    """See https://alpha.com and https://beta.com for details."""
    pass
'''
        _, edges = PythonASTChunker().chunk_with_edges(code, "x.py")
        refs = {e.target_label for e in edges if e.edge_type == "REFERENCES"}
        assert "https://alpha.com" in refs
        assert "https://beta.com" in refs


# ===========================================================================
# capture — URL extraction produces reference memories
# ===========================================================================


class _FakeLLMAdapter:
    """Minimal stub that simulates LLM returning a fixed JSON list."""

    def __init__(self, response: str):
        self._response = response

    def is_passthrough(self) -> bool:
        return False

    async def complete(self, system: str, user: str) -> str:
        return self._response


class _FakeMemoryManager:
    """Records create_memory calls without touching SQLite."""

    def __init__(self):
        self.created: list[dict] = []

    async def search_similar(self, params, *, cwd=None, content_types=None):
        return []

    async def create_memory(self, params, *, cwd=None):
        self.created.append({"type": params.type, "content": params.content})
        m = MagicMock()
        m.id = f"mem-{len(self.created)}"
        return m


class TestCaptureUrlExtraction:
    def test_two_urls_one_duplicated_produces_two_reference_memories(self):
        text = (
            "Check https://alpha.com/docs and https://beta.com/api "
            "and again https://alpha.com/docs for more."
        )
        llm = _FakeLLMAdapter("[]")
        mm = _FakeMemoryManager()
        params = CaptureParams(text=text)

        asyncio.run(capture(params, mm, llm))

        ref_memories = [c for c in mm.created if c["type"] == "reference"]
        urls = {c["content"] for c in ref_memories}
        assert "https://alpha.com/docs" in urls
        assert "https://beta.com/api" in urls
        assert len(urls) == 2, f"expected 2 unique reference memories, got {urls}"

    def test_reference_type_stored(self):
        text = "See https://example.com/setup for install steps."
        llm = _FakeLLMAdapter("[]")
        mm = _FakeMemoryManager()
        params = CaptureParams(text=text)

        asyncio.run(capture(params, mm, llm))

        ref_memories = [c for c in mm.created if c["type"] == "reference"]
        assert ref_memories, "expected at least one reference memory"
        assert ref_memories[0]["content"] == "https://example.com/setup"

    def test_no_urls_no_reference_memories(self):
        text = "Just some plain text with no links."
        llm = _FakeLLMAdapter("[]")
        mm = _FakeMemoryManager()
        params = CaptureParams(text=text)

        asyncio.run(capture(params, mm, llm))

        ref_memories = [c for c in mm.created if c["type"] == "reference"]
        assert ref_memories == []

    def test_normalize_url_dedup_trailing_slash(self):
        text = "See https://example.com/ and https://example.com here."
        llm = _FakeLLMAdapter("[]")
        mm = _FakeMemoryManager()
        params = CaptureParams(text=text)

        asyncio.run(capture(params, mm, llm))

        ref_memories = [c for c in mm.created if c["type"] == "reference"]
        assert len(ref_memories) == 1, f"expected 1 after trailing-slash dedup, got {ref_memories}"


# ===========================================================================
# types — reference in MemoryContentType and PERSISTENT_MEMORY_TYPES
# ===========================================================================


class TestReferenceType:
    def test_reference_in_memory_content_type_literal(self):
        assert "reference" in get_args(MemoryContentType)

    def test_reference_in_persistent_memory_types(self):
        assert "reference" in PERSISTENT_MEMORY_TYPES
