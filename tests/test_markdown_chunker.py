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


def test_documents_edges_resolve_end_to_end():
    # Regression guard: the DOCUMENTS edge source_qualname MUST equal the
    # qualname the indexer registers for the heading chunk, or resolve_edges
    # silently drops the edge (it skips edges with an unknown source). The
    # indexer now registers chunk.qualname verbatim — simulate that here.
    from mememo.core.symbol_resolver import SymbolEntry, resolve_edges

    md = "# Guide\n\nUses `StorageManager` for persistence.\n"
    chunks, edges = MarkdownChunker().chunk_with_edges(md, "docs/guide.md")

    symbols = [
        SymbolEntry(memory_id=f"doc{i}", qualname=c.qualname)
        for i, c in enumerate(chunks)
        if c.qualname
    ]
    assert symbols, "heading chunk must carry a qualname for the indexer to register"
    symbols.append(
        SymbolEntry(memory_id="code1", qualname="mememo.core.storage_manager.StorageManager")
    )

    rels = resolve_edges(edges, repo_id="r", branch="main", commit_sha="a" * 40, symbols=symbols)
    docs = [r for r in rels if r.type == "DOCUMENTS"]
    assert docs, "DOCUMENTS edge was dropped — source qualname mismatch regression"
    assert any(r.target_memory_id == "code1" for r in docs)


def test_heading_chunk_line_range_is_inclusive():
    chunks, _ = MarkdownChunker().chunk_with_edges("# A\nline1\nline2\n", "x.md")
    h = next(c for c in chunks if c.function_name == "A")
    assert h.start_line == 1
    assert h.end_line == 3  # heading line + 2 body lines (was off-by-one: 2)


def test_url_is_not_emitted_as_a_documents_path_target():
    """The path scan still strips URLs so a link like https://host/x.py
    doesn't masquerade as a repo path target."""
    md = "# A\n\nSee https://example.com/docs/setup.py for details.\n"
    _, edges = MarkdownChunker().chunk_with_edges(md, "x.md")
    docs_targets = {e.target_label for e in _by_type(edges).get("DOCUMENTS", [])}
    assert not any("example.com" in t for t in docs_targets)


def test_url_emits_references_edge():
    """v0.8: URLs in prose become REFERENCES edges from the doc section."""
    md = "# Setup\n\nSee https://example.com/docs for details.\n"
    _, edges = MarkdownChunker().chunk_with_edges(md, "x.md")
    refs = _by_type(edges).get("REFERENCES", [])
    assert refs, "expected a REFERENCES edge for the URL"
    assert {e.target_label for e in refs} == {"https://example.com/docs"}
    assert all(e.confidence == "INFERRED" for e in refs)
    assert all(e.source_qualname.startswith("x.setup") for e in refs)


def test_url_trailing_punctuation_stripped():
    """Sentence punctuation right after a URL is not part of the URL."""
    md = "# A\n\nSee https://example.com/a, https://example.com/b. Also (https://example.com/c).\n"
    _, edges = MarkdownChunker().chunk_with_edges(md, "x.md")
    targets = {e.target_label for e in _by_type(edges).get("REFERENCES", [])}
    assert targets == {
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
    }


def test_url_in_fence_is_not_referenced():
    """URLs inside fenced code blocks are content, not REFERENCES."""
    md = "# A\n\n```text\nhttps://fenced.example.com/x\n```\n"
    _, edges = MarkdownChunker().chunk_with_edges(md, "x.md")
    assert "REFERENCES" not in _by_type(edges)


def test_duplicate_urls_dedup_per_section():
    md = "# A\n\nFirst https://example.com/x and again https://example.com/x .\n"
    _, edges = MarkdownChunker().chunk_with_edges(md, "x.md")
    refs = _by_type(edges).get("REFERENCES", [])
    assert [e.target_label for e in refs] == ["https://example.com/x"]


def test_references_edge_survives_resolver_as_unresolved_symbol():
    """v0.8: REFERENCES edges must reach storage with target_memory_id=NULL
    and target_symbol=<url> — the resolver should not silently drop them."""
    from mememo.core.symbol_resolver import SymbolEntry, resolve_edges

    md = "# Guide\n\nSee https://docs.example.com/setup for the install path.\n"
    chunks, edges = MarkdownChunker().chunk_with_edges(md, "docs/guide.md")
    symbols = [
        SymbolEntry(memory_id=f"doc{i}", qualname=c.qualname)
        for i, c in enumerate(chunks)
        if c.qualname
    ]
    rels = resolve_edges(edges, repo_id="r", branch="main", commit_sha="a" * 40, symbols=symbols)
    refs = [r for r in rels if r.type == "REFERENCES"]
    assert refs, "REFERENCES edge dropped by resolver"
    assert refs[0].target_memory_id is None
    assert refs[0].target_symbol == "https://docs.example.com/setup"
