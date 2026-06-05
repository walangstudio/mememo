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
    "go": (
        "tree_sitter_go",
        "a.go",
        "package p\ntype A struct {\n  owner string\n  balance int\n}\n"
        "func (a *A) m() { local := 1; _ = local }\n",
        {"owner", "balance"},
    ),
    "ruby": (
        "tree_sitter_ruby",
        "a.rb",
        "class A\n  def initialize\n    @owner = 'x'\n    @balance = 0\n  end\n"
        "  def m\n    local = 1\n  end\nend\n",
        {"owner", "balance"},
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


def test_go_embedded_field_skipped_and_multiname_captured() -> None:
    pytest.importorskip("tree_sitter_go")
    names = _fields("package p\ntype A struct {\n  a, b int\n  Logger\n}\n", "a.go", "go")
    assert {"a", "b"} <= names
    # an anonymous embedded field (type only, no field name) contributes nothing
    assert "Logger" not in names


def test_go_struct_chunk_and_method_share_class_name() -> None:
    # The struct must become a class chunk AND its methods must carry class_name
    # so the class diagram attaches the methods to the struct.
    pytest.importorskip("tree_sitter_go")
    src = "package p\ntype A struct {\n  x int\n}\nfunc (a *A) m() {}\n"
    chunks, _ = TreeSitterChunker().chunk_with_edges(src, "a.go", "go")
    cls = next(c for c in chunks if c.chunk_type == "class")
    assert cls.class_name == "A"
    meth = next(c for c in chunks if c.chunk_type == "method")
    assert meth.class_name == "A"


def test_ruby_ivars_collected_across_methods() -> None:
    pytest.importorskip("tree_sitter_ruby")
    src = "class A\n  def initialize\n    @a = 1\n  end\n  def setup\n    @b = 2\n  end\nend\n"
    assert {"a", "b"} <= _fields(src, "a.rb", "ruby")


def test_ruby_nested_class_ivars_not_leaked_into_outer() -> None:
    pytest.importorskip("tree_sitter_ruby")
    src = (
        "class Outer\n  def initialize\n    @outer_field = 1\n  end\n"
        "  class Inner\n    def initialize\n      @inner_field = 2\n    end\n  end\nend\n"
    )
    chunks, _ = TreeSitterChunker().chunk_with_edges(src, "o.rb", "ruby")
    outer = next(c for c in chunks if c.class_name == "Outer")
    names = set(outer.attributes or [])
    assert "outer_field" in names
    assert "inner_field" not in names


def _attrs(src: str, fp: str, lang: str) -> list[str]:
    chunks, _ = TreeSitterChunker().chunk_with_edges(src, fp, lang)
    cls = next(c for c in chunks if c.chunk_type == "class")
    return cls.attributes or []


# (lang, grammar, file, src, expected "name: type" entries)
TYPED_CASES = [
    (
        "go",
        "tree_sitter_go",
        "a.go",
        "package p\ntype A struct {\n  owner string\n}\n",
        "owner: string",
    ),
    ("rust", "tree_sitter_rust", "a.rs", "struct A {\n  balance: i32,\n}\n", "balance: i32"),
    ("java", "tree_sitter_java", "A.java", "class A {\n  int balance;\n}\n", "balance: int"),
    (
        "csharp",
        "tree_sitter_c_sharp",
        "A.cs",
        "class A {\n  private int balance;\n}\n",
        "balance: int",
    ),
    ("cpp", "tree_sitter_cpp", "A.cpp", "class A {\n  int balance;\n};\n", "balance: int"),
    (
        "typescript",
        "tree_sitter_typescript",
        "A.ts",
        "class A {\n  balance: number;\n}\n",
        "balance: number",
    ),
    (
        "kotlin",
        "tree_sitter_kotlin",
        "A.kt",
        'class A {\n  val owner: String = ""\n}\n',
        "owner: String",
    ),
    (
        "swift",
        "tree_sitter_swift",
        "A.swift",
        "class A {\n  let balance: Int = 0\n}\n",
        "balance: Int",
    ),
    (
        "scala",
        "tree_sitter_scala",
        "A.scala",
        'class A {\n  val owner: String = ""\n}\n',
        "owner: String",
    ),
    (
        "php",
        "tree_sitter_php",
        "A.php",
        "<?php\nclass A {\n  private int $balance;\n}\n",
        "balance: int",
    ),
]


@pytest.mark.parametrize(
    "lang,grammar,fp,src,expected", TYPED_CASES, ids=[c[0] for c in TYPED_CASES]
)
def test_field_type_captured(lang, grammar, fp, src, expected) -> None:
    pytest.importorskip(grammar)
    assert expected in _attrs(src, fp, lang)


def test_go_multiname_field_shares_type() -> None:
    pytest.importorskip("tree_sitter_go")
    attrs = _attrs("package p\ntype A struct {\n  a, b int\n}\n", "a.go", "go")
    assert "a: int" in attrs and "b: int" in attrs


def test_javascript_field_has_no_type() -> None:
    # JS fields are untyped; the entry stays a bare name (no trailing ": ...").
    pytest.importorskip("tree_sitter_javascript")
    assert _attrs("class A {\n  balance = 0;\n}\n", "A.js", "javascript") == ["balance"]


def test_cpp_global_namespace_qualifier_preserved() -> None:
    # The leading '::' of a global-namespace type is part of the type, not an
    # annotation colon, so it must survive (only ': T' annotation forms strip).
    pytest.importorskip("tree_sitter_cpp")
    attrs = _attrs("class A {\n  ::std::string owner;\n};\n", "A.cpp", "cpp")
    assert "owner: ::std::string" in attrs


def test_kotlin_nullable_and_generic_types_captured() -> None:
    pytest.importorskip("tree_sitter_kotlin")
    src = "class A {\n  val a: String? = null\n  val b: Map<String, Int> = mapOf()\n}\n"
    attrs = _attrs(src, "A.kt", "kotlin")
    assert "a: String?" in attrs and "b: Map<String, Int>" in attrs


def test_nested_class_fields_not_leaked_into_outer() -> None:
    pytest.importorskip("tree_sitter_java")
    src = "class Outer {\n  int outerField;\n  class Inner {\n    int innerField;\n  }\n}\n"
    chunks, _ = TreeSitterChunker().chunk_with_edges(src, "O.java", "java")
    outer = next(c for c in chunks if c.class_name == "Outer")
    names = {a.split(":")[0].strip() for a in (outer.attributes or [])}
    assert "outerField" in names
    assert "innerField" not in names
