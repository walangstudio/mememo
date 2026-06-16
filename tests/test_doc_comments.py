"""Doc-comment extraction for tree-sitter languages (v0.47).

The walkers emit chunks but never the doc comment (it lives outside the
definition node, as a preceding sibling). ``attach_doc_comments`` fills
``Chunk.docstring`` in a post-pass, and the FTS lexical index folds that text
in so doc comments are searchable. Tests cover the comment forms per language,
the adjacency / attribute-skip / parent-hop edge cases, the no-false-positive
cases, and the FTS fold. Each language test skips if its grammar isn't
installed.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")

from mememo.chunking.tree_sitter_chunker import TreeSitterChunker  # noqa: E402
from mememo.core.storage_manager import StorageManager  # noqa: E402
from mememo.types.memory import (  # noqa: E402
    NULL_SHA,
    BranchContext,
    Memory,
    MemoryContent,
    MemoryMetadata,
    MemoryRelationships,
    MemorySummary,
    RepoContext,
)
from mememo.utils.hashing import calculate_checksum  # noqa: E402

# mememo language name -> tree-sitter grammar module to importorskip on.
_GRAMMAR_MODULE = {
    "java": "tree_sitter_java",
    "rust": "tree_sitter_rust",
    "go": "tree_sitter_go",
    "typescript": "tree_sitter_typescript",
    "ruby": "tree_sitter_ruby",
    "kotlin": "tree_sitter_kotlin",
    "csharp": "tree_sitter_c_sharp",
    "php": "tree_sitter_php",
    "swift": "tree_sitter_swift",
    "scala": "tree_sitter_scala",
    "cpp": "tree_sitter_cpp",
}


@pytest.fixture(scope="module")
def chunker() -> TreeSitterChunker:
    return TreeSitterChunker()


def _docs(chunker: TreeSitterChunker, language: str, code: str, path: str) -> dict[str, str]:
    """Map symbol name -> docstring for the function/method/class chunks."""
    pytest.importorskip(_GRAMMAR_MODULE[language])
    chunks, _ = chunker.chunk_with_edges(code, path, language)
    return {
        (c.function_name or c.class_name): c.docstring
        for c in chunks
        if c.chunk_type in ("function", "method", "class")
    }


def test_java_block_doc_on_class_and_method(chunker: TreeSitterChunker) -> None:
    code = (
        "/** Greets the world. */\n"
        "public class Hello {\n"
        "    /**\n"
        "     * Prints a greeting.\n"
        "     * @param name who\n"
        "     */\n"
        "    void greet(String name) {}\n"
        "}\n"
    )
    docs = _docs(chunker, "java", code, "Hello.java")
    assert docs["Hello"] == "Greets the world."
    assert docs["greet"] == "Prints a greeting.\n@param name who"


def test_rust_line_doc_skips_attribute(chunker: TreeSitterChunker) -> None:
    # The ``///`` doc sits above a ``#[inline]`` attribute, which must be
    # scanned past — and Rust line comments roll their end point past the
    # newline, exercising the adjacency fix.
    code = (
        "/// Adds two numbers.\n"
        "#[inline]\n"
        "pub fn add(a: i32, b: i32) -> i32 { a + b }\n"
        "\n"
        "/// A point in 2D.\n"
        "pub struct Point { x: i32, y: i32 }\n"
    )
    docs = _docs(chunker, "rust", code, "a.rs")
    assert docs["add"] == "Adds two numbers."
    assert docs["Point"] == "A point in 2D."


def test_go_godoc_on_func_and_type(chunker: TreeSitterChunker) -> None:
    code = (
        "package main\n\n"
        "// Add returns the sum of a and b.\n"
        "func Add(a, b int) int { return a + b }\n\n"
        "// Point is a 2D point.\n"
        "type Point struct { X, Y int }\n"
    )
    docs = _docs(chunker, "go", code, "a.go")
    assert docs["Add"] == "Add returns the sum of a and b."
    assert docs["Point"] == "Point is a 2D point."  # godoc binds to ``type``


def test_go_skips_build_directive(chunker: TreeSitterChunker) -> None:
    code = "package main\n\n//go:noinline\nfunc Bare() {}\n"
    docs = _docs(chunker, "go", code, "a.go")
    assert docs["Bare"] is None  # //go: directives are not documentation


def test_go_grouped_type_block_per_spec_docs(chunker: TreeSitterChunker) -> None:
    # Each spec in a ``type ( ... )`` block takes its own preceding comment.
    code = (
        "package main\n\n"
        "type (\n"
        "\t// A is the first.\n"
        "\tA struct{ X int }\n"
        "\t// B is the second.\n"
        "\tB struct{ Y int }\n"
        ")\n"
    )
    docs = _docs(chunker, "go", code, "a.go")
    assert docs["A"] == "A is the first."
    assert docs["B"] == "B is the second."


def test_typescript_block_doc(chunker: TreeSitterChunker) -> None:
    code = (
        "/** Adds two numbers. */\n"
        "function add(a: number, b: number): number { return a + b; }\n\n"
        "/** A widget. */\n"
        "class Widget {}\n"
    )
    docs = _docs(chunker, "typescript", code, "a.ts")
    assert docs["add"] == "Adds two numbers."
    assert docs["Widget"] == "A widget."


def test_ruby_hash_doc_via_parent_hop(chunker: TreeSitterChunker) -> None:
    # The method's leading ``#`` comment is a child of the class node (sibling of
    # the body_statement wrapper), so it is reached only by hopping to the parent.
    code = (
        "# Greets the world.\nclass Hello\n  # Prints a greeting.\n  def greet(name)\n  end\nend\n"
    )
    docs = _docs(chunker, "ruby", code, "a.rb")
    assert docs["Hello"] == "Greets the world."
    assert docs["greet"] == "Prints a greeting."


def test_kotlin_kdoc(chunker: TreeSitterChunker) -> None:
    code = (
        "/** A greeter. */\nclass Hello {\n    /** Greets. */\n    fun greet(name: String) {}\n}\n"
    )
    docs = _docs(chunker, "kotlin", code, "a.kt")
    assert docs["Hello"] == "A greeter."
    assert docs["greet"] == "Greets."


def test_csharp_xml_doc(chunker: TreeSitterChunker) -> None:
    code = (
        "/// <summary>A greeter.</summary>\n"
        "public class Hello {\n"
        "    /// <summary>Greets.</summary>\n"
        "    void Greet(string name) {}\n"
        "}\n"
    )
    docs = _docs(chunker, "csharp", code, "a.cs")
    assert docs["Hello"] == "<summary>A greeter.</summary>"
    assert docs["Greet"] == "<summary>Greets.</summary>"


def test_php_phpdoc(chunker: TreeSitterChunker) -> None:
    code = (
        "<?php\n/** A greeter. */\nclass Hello {\n"
        "    /** Greets someone. */\n    function greet($name) {}\n}\n"
    )
    docs = _docs(chunker, "php", code, "a.php")
    assert docs["Hello"] == "A greeter."
    assert docs["greet"] == "Greets someone."


def test_blank_line_gap_detaches(chunker: TreeSitterChunker) -> None:
    code = "package main\n\n// orphaned by the blank line below\n\nfunc Bare() {}\n"
    docs = _docs(chunker, "go", code, "a.go")
    assert docs["Bare"] is None


def test_plain_comments_are_not_docs(chunker: TreeSitterChunker) -> None:
    # A plain ``//`` line and a single-star ``/* */`` block are ordinary
    # comments in TypeScript, not documentation.
    code = (
        "// just a regular comment\n"
        "function plain() {}\n\n"
        "/* not a doc block */\n"
        "function plain2() {}\n"
    )
    docs = _docs(chunker, "typescript", code, "b.ts")
    assert docs["plain"] is None
    assert docs["plain2"] is None


def _mem(repo_id: str, text: str, docstring: str | None) -> Memory:
    return Memory(
        id=str(uuid.uuid4()),
        repo=RepoContext(id=repo_id, name="r", path=str(Path.cwd()), remote_url=None),
        branch=BranchContext(name="main", commit_hash=NULL_SHA),
        content=MemoryContent(type="code_snippet", text=text, docstring=docstring, language="rust"),
        metadata=MemoryMetadata(
            checksum=calculate_checksum(text + (docstring or "")),
            token_count=len(text.split()),
            created_at_sha=NULL_SHA,
            updated_at_sha=NULL_SHA,
        ),
        relationships=MemoryRelationships(),
        summary=MemorySummary(one_line="snippet"),
    )


@pytest.mark.asyncio
async def test_docstring_is_lexically_searchable(tmp_path) -> None:
    """A term that appears only in the doc comment surfaces the memory via FTS,
    because the docstring is folded into the lexical index."""
    storage = StorageManager(base_dir=tmp_path / "store")
    # 'Levenshtein' appears in the docstring only, never in the code body.
    body = "fn distance(a: &str, b: &str) -> usize { a.len() + b.len() }"
    assert "Levenshtein" not in body
    documented = _mem("r1", body, "Computes the Levenshtein edit distance.")
    plain = _mem("r1", body, None)
    await storage.save_memory(documented)
    await storage.save_memory(plain)

    hits = storage.search_fts("Levenshtein", "r1", "main", 5)
    assert documented.id in hits  # found via the folded docstring
    assert plain.id not in hits  # no docstring -> term not indexed


@pytest.mark.asyncio
async def test_backfill_folds_docstring(tmp_path) -> None:
    """A rebuilt FTS index (the _backfill_fts path) keeps doc comments
    searchable, matching the live save_memory insert."""
    storage = StorageManager(base_dir=tmp_path / "store")
    body = "fn dist(a: &str, b: &str) -> usize { 0 }"
    assert "Damerau" not in body
    mem = _mem("r1", body, "Computes the Damerau edit distance.")
    await storage.save_memory(mem)
    # Simulate a wiped / legacy FTS row, then re-populate via backfill.
    storage.conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (mem.id,))
    storage.conn.commit()
    storage._backfill_fts()
    assert mem.id in storage.search_fts("Damerau", "r1", "main", 5)
