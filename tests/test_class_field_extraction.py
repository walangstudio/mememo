"""Tree-sitter class-field extraction -> Chunk.attributes.

Generalizes the Python class-field extraction (v0.20.0) to the typed OO
tree-sitter languages so their class diagrams show fields too. Each case asserts
the class chunk carries the declared field names and excludes method locals.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tree_sitter")

from mememo.chunking.tree_sitter_chunker import TreeSitterChunker  # noqa: E402

CASES = {
    "java": (
        "tree_sitter_java",
        "A.java",
        "class A {\n  private int balance;\n  String owner;\n" "  void m() { int local = 1; }\n}\n",
        {"balance", "owner"},
    ),
    "csharp": (
        "tree_sitter_c_sharp",
        "A.cs",
        "class A {\n  private int balance;\n  public string Owner { get; set; }\n  void M() {}\n}\n",
        {"balance", "Owner"},
    ),
    "cpp": (
        "tree_sitter_cpp",
        "A.cpp",
        "class A {\n  int balance;\n  std::string owner;\n  void m() {}\n};\n",
        {"balance", "owner"},
    ),
    "typescript": (
        "tree_sitter_typescript",
        "A.ts",
        "class A {\n  balance: number;\n  private owner: string;\n  m() {}\n}\n",
        {"balance", "owner"},
    ),
    "rust": (
        "tree_sitter_rust",
        "a.rs",
        "struct A {\n  owner: String,\n  balance: i32,\n}\n",
        {"owner", "balance"},
    ),
    "javascript": (
        "tree_sitter_javascript",
        "A.js",
        "class A {\n  balance = 0;\n  #owner = 'x';\n  m() { let local = 1; }\n}\n",
        {"balance", "owner"},
    ),
    "kotlin": (
        "tree_sitter_kotlin",
        "A.kt",
        'class A {\n  val owner: String = ""\n  var balance: Int = 0\n  fun m() {}\n}\n',
        {"owner", "balance"},
    ),
    "swift": (
        "tree_sitter_swift",
        "A.swift",
        'class A {\n  var owner: String = ""\n  let balance: Int = 0\n  func m() {}\n}\n',
        {"owner", "balance"},
    ),
    "scala": (
        "tree_sitter_scala",
        "A.scala",
        'class A {\n  val owner: String = ""\n  var balance: Int = 0\n  def m(): Unit = {}\n}\n',
        {"owner", "balance"},
    ),
    "php": (
        "tree_sitter_php",
        "A.php",
        "<?php\nclass A {\n  private int $balance;\n  public string $owner;\n  function m() {}\n}\n",
        {"balance", "owner"},
    ),
}


@pytest.mark.parametrize("lang", list(CASES))
def test_class_fields_extracted(lang: str) -> None:
    grammar, fp, src, expected = CASES[lang]
    pytest.importorskip(grammar)
    chunks, _ = TreeSitterChunker().chunk_with_edges(src, fp, lang)
    cls = next((c for c in chunks if c.chunk_type == "class"), None)
    assert cls is not None, f"no class chunk for {lang}"
    attrs = {a.split(":")[0].strip().lstrip("#") for a in (cls.attributes or [])}
    assert expected <= attrs, f"{lang}: expected {expected}, got {attrs}"
    # method locals must not leak in as fields
    assert "local" not in attrs


def _fields(src: str, fp: str, lang: str) -> set[str]:
    chunks, _ = TreeSitterChunker().chunk_with_edges(src, fp, lang)
    cls = next(c for c in chunks if c.chunk_type == "class")
    return {a.split(":")[0].strip().lstrip("#") for a in (cls.attributes or [])}


def test_multi_declarator_fields_all_captured() -> None:
    pytest.importorskip("tree_sitter_java")
    assert {"a", "b", "c"} <= _fields("class A {\n  int a, b, c;\n}\n", "A.java", "java")


def test_cpp_pointer_and_array_fields_are_clean_names() -> None:
    pytest.importorskip("tree_sitter_cpp")
    names = _fields("class A {\n  int* p;\n  int arr[4];\n  int& r;\n}\n", "A.cpp", "cpp")
    assert {"p", "arr", "r"} <= names
    # no declarator punctuation leaks into the name
    assert not any(any(c in n for c in "*&[]") for n in names)


def test_rust_enum_variant_fields_not_leaked() -> None:
    pytest.importorskip("tree_sitter_rust")
    chunks, _ = TreeSitterChunker().chunk_with_edges(
        "enum E {\n  A,\n  B(i32),\n  C { z: i32 },\n}\n", "e.rs", "rust"
    )
    e = next(c for c in chunks if c.class_name == "E")
    assert not (e.attributes or [])  # an enum has variants, not data fields


def test_scala_method_local_vals_not_treated_as_fields() -> None:
    # Scala uses val_definition for both class fields AND method locals, so the
    # walk must not descend into method bodies.
    pytest.importorskip("tree_sitter_scala")
    src = "class A {\n  val field = 1\n  def m(): Int = { val localv = 2; localv }\n}\n"
    names = _fields(src, "A.scala", "scala")
    assert "field" in names
    assert "localv" not in names


def test_swift_computed_property_accessor_locals_not_fields() -> None:
    pytest.importorskip("tree_sitter_swift")
    src = "class A {\n  let stored = 0\n  var area: Int { let tmp = stored; return tmp }\n}\n"
    names = _fields(src, "A.swift", "swift")
    assert "stored" in names and "area" in names
    assert "tmp" not in names


def test_kotlin_custom_getter_locals_not_fields() -> None:
    pytest.importorskip("tree_sitter_kotlin")
    src = "class A {\n  val items = 0\n  val size: Int\n    get() { val tmp = items; return tmp }\n}\n"
    names = _fields(src, "A.kt", "kotlin")
    assert "items" in names and "size" in names
    assert "tmp" not in names


def test_php_multiple_properties_one_declaration() -> None:
    pytest.importorskip("tree_sitter_php")
    names = _fields("<?php\nclass A {\n  public $a, $b;\n}\n", "A.php", "php")
    assert {"a", "b"} <= names


def test_nested_class_fields_not_leaked_into_outer() -> None:
    pytest.importorskip("tree_sitter_java")
    src = "class Outer {\n  int outerField;\n  class Inner {\n    int innerField;\n  }\n}\n"
    chunks, _ = TreeSitterChunker().chunk_with_edges(src, "O.java", "java")
    outer = next(c for c in chunks if c.class_name == "Outer")
    names = {a.split(":")[0].strip() for a in (outer.attributes or [])}
    assert "outerField" in names
    assert "innerField" not in names
