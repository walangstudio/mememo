"""Markdown chunker — heading-scoped chunks + DOCUMENTS edges (v0.7).

Pure-Python chunker, so no tree-sitter import guard is needed.
"""

from __future__ import annotations

from mememo.chunking.markdown_chunker import MarkdownChunker

SAMPLE = """\
Intro paragraph before any heading.

# Architecture

Overview of the system. The core type is `StorageManager` and entry is `main()`.

## Storage

Lives in `mememo/core/storage_manager.py`. Uses the `relations` table.

```python
# fenced code must NOT contribute symbols
ignored_symbol()
```

## Resolution

The `resolve_edges()` pass walks `SymbolResolver`.

# Limitations

No images yet.
"""


def _by_type(edges):
    out: dict[str, list] = {}
    for e in edges:
        out.setdefault(e.edge_type, []).append(e)
    return out


def test_headings_become_chunks():
    chunks, _ = MarkdownChunker().chunk_with_edges(SAMPLE, "docs/design.md")
    headings = {c.function_name for c in chunks if c.chunk_type == "heading"}
    assert {"Architecture", "Storage", "Resolution", "Limitations"} <= headings


def test_subsection_parent_is_recorded():
    chunks, _ = MarkdownChunker().chunk_with_edges(SAMPLE, "docs/design.md")
    storage = next(c for c in chunks if c.function_name == "Storage")
    assert storage.parent_class == "Architecture"
    arch = next(c for c in chunks if c.function_name == "Architecture")
    assert arch.parent_class is None  # top-level


def test_preamble_kept_as_text_chunk():
    chunks, _ = MarkdownChunker().chunk_with_edges(SAMPLE, "docs/design.md")
    text_chunks = [c for c in chunks if c.chunk_type == "text"]
    assert any("Intro paragraph" in c.text for c in text_chunks)


def test_documents_edges_from_backtick_symbols():
    _, edges = MarkdownChunker().chunk_with_edges(SAMPLE, "docs/design.md")
    docs = _by_type(edges).get("DOCUMENTS", [])
    targets = {e.target_label for e in docs}
    assert "StorageManager" in targets  # CamelCase symbol
    assert "main()" not in targets and "main" in targets  # call parens stripped
    assert "resolve_edges" in targets
    assert "SymbolResolver" in targets
    assert all(e.confidence == "INFERRED" for e in docs)


def test_documents_edge_source_is_namespaced_qualname():
    _, edges = MarkdownChunker().chunk_with_edges(SAMPLE, "docs/design.md")
    docs = _by_type(edges).get("DOCUMENTS", [])
    # Storage section symbols are sourced under module.architecture.storage
    storage_edges = [e for e in docs if e.source_qualname.endswith("architecture.storage")]
    assert storage_edges
    assert all(e.source_qualname.startswith("docs.design") for e in docs)


def test_file_path_mention_becomes_edge():
    _, edges = MarkdownChunker().chunk_with_edges(SAMPLE, "docs/design.md")
    targets = {e.target_label for e in _by_type(edges).get("DOCUMENTS", [])}
    assert "mememo/core/storage_manager.py" in targets


def test_fenced_code_symbols_are_ignored():
    _, edges = MarkdownChunker().chunk_with_edges(SAMPLE, "docs/design.md")
    targets = {e.target_label for e in _by_type(edges).get("DOCUMENTS", [])}
    assert "ignored_symbol" not in targets


def test_empty_and_headingless_docs_do_not_crash():
    assert MarkdownChunker().chunk_with_edges("", "x.md") == ([], [])
    chunks, edges = MarkdownChunker().chunk_with_edges("just prose, no heading", "x.md")
    assert chunks and chunks[0].chunk_type == "text"
    assert edges == []
