"""
Tree-sitter multi-language chunker.

Uses tree-sitter to parse and extract code structures from multiple languages:
- TypeScript/JavaScript (functions, classes, methods, interfaces)
- Go (functions, methods, structs, interfaces)
- Rust (functions, impl blocks, structs, traits)
- Java (classes, methods, interfaces)
- C/C++ (functions, classes, structs)
- C# (classes, methods, interfaces)
"""

import logging

from .base_chunker import BaseChunker, Chunk

logger = logging.getLogger(__name__)

# Try to import tree-sitter packages
try:
    from tree_sitter_languages import get_language, get_parser

    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False
    logger.warning("tree-sitter-languages not available, tree-sitter chunker disabled")


# Language-specific query patterns
LANGUAGE_QUERIES = {
    "typescript": """
        (function_declaration
            name: (identifier) @function.name) @function.def

        (method_definition
            name: (property_identifier) @method.name) @method.def

        (class_declaration
            name: (type_identifier) @class.name) @class.def

        (interface_declaration
            name: (type_identifier) @interface.name) @interface.def
    """,
    "javascript": """
        (function_declaration
            name: (identifier) @function.name) @function.def

        (method_definition
            name: (property_identifier) @method.name) @method.def

        (class_declaration
            name: (identifier) @class.name) @class.def
    """,
    "go": """
        (function_declaration
            name: (identifier) @function.name) @function.def

        (method_declaration
            name: (field_identifier) @method.name
            receiver: (parameter_list) @method.receiver) @method.def

        (type_declaration
            (type_spec
                name: (type_identifier) @type.name)) @type.def
    """,
    "rust": """
        (function_item
            name: (identifier) @function.name) @function.def

        (impl_item) @impl.def

        (struct_item
            name: (type_identifier) @struct.name) @struct.def

        (trait_item
            name: (type_identifier) @trait.name) @trait.def
    """,
    "java": """
        (class_declaration
            name: (identifier) @class.name) @class.def

        (method_declaration
            name: (identifier) @method.name) @method.def

        (interface_declaration
            name: (identifier) @interface.name) @interface.def
    """,
    "c": """
        (function_definition
            declarator: (function_declarator
                declarator: (identifier) @function.name)) @function.def

        (struct_specifier
            name: (type_identifier) @struct.name) @struct.def
    """,
    "cpp": """
        (function_definition
            declarator: (function_declarator
                declarator: (identifier) @function.name)) @function.def

        (class_specifier
            name: (type_identifier) @class.name) @class.def

        (struct_specifier
            name: (type_identifier) @struct.name) @struct.def
    """,
    "csharp": """
        (class_declaration
            name: (identifier) @class.name) @class.def

        (method_declaration
            name: (identifier) @method.name) @method.def

        (interface_declaration
            name: (identifier) @interface.name) @interface.def
    """,
}


class TreeSitterChunker(BaseChunker):
    """
    Tree-sitter based chunker for multiple languages.

    Supports:
    - TypeScript/JavaScript: functions, classes, methods, interfaces
    - Go: functions, methods, structs
    - Rust: functions, impl blocks, structs, traits
    - Java: classes, methods, interfaces
    - C/C++: functions, classes, structs
    - C#: classes, methods, interfaces
    """

    def __init__(self):
        """Initialize tree-sitter chunker with lazy-loaded parsers."""
        self._parsers: dict = {}
        self._languages: dict = {}
        self._failed_languages: set = set()

        if not TREE_SITTER_AVAILABLE:
            raise RuntimeError(
                "tree-sitter-languages not installed. "
                "Install with: pip install tree-sitter-languages"
            )

    def _get_parser(self, language: str):
        """
        Get or create parser for language (lazy loading).

        Args:
            language: Programming language

        Returns:
            Tree-sitter parser
        """
        if language in self._failed_languages:
            raise ValueError(f"tree-sitter parser unavailable for {language}")

        if language not in self._parsers:
            try:
                self._languages[language] = get_language(language)
                self._parsers[language] = get_parser(language)
                logger.debug(f"Loaded tree-sitter parser for {language}")
            except Exception as e:
                self._failed_languages.add(language)
                logger.warning(f"tree-sitter parser unavailable for {language}: {e}")
                raise ValueError(f"tree-sitter parser unavailable for {language}") from e

        return self._parsers[language]

    def chunk(self, code: str, file_path: str, language: str = None) -> list[Chunk]:
        """
        Chunk code using tree-sitter parsing.

        Args:
            code: Source code content
            file_path: Path to file
            language: Programming language (auto-detected if not provided)

        Returns:
            List of code chunks

        Raises:
            ValueError: If language not supported
        """
        # Auto-detect language if not provided
        if language is None:
            from .language_detector import detect_language

            language = detect_language(file_path)
            if language is None:
                raise ValueError(f"Cannot detect language for {file_path}")

        # Check if language supported
        if language not in LANGUAGE_QUERIES:
            raise ValueError(f"Tree-sitter chunking not supported for {language}")

        # Get parser
        parser = self._get_parser(language)
        tree = parser.parse(bytes(code, "utf8"))

        # Get query for language
        query_text = LANGUAGE_QUERIES[language]
        language_obj = self._languages[language]
        query = language_obj.query(query_text)

        # Extract captures
        captures = query.captures(tree.root_node)

        chunks = []
        processed_nodes = set()  # Avoid duplicates

        for node, capture_name in captures:
            # Skip if already processed
            node_id = (node.start_point, node.end_point)
            if node_id in processed_nodes:
                continue
            processed_nodes.add(node_id)

            # Only process definition nodes (not just names)
            if not capture_name.endswith(".def"):
                continue

            chunk = self._extract_node(node, code, capture_name, language, file_path)
            if chunk:
                chunks.append(chunk)

        logger.debug(f"Tree-sitter extracted {len(chunks)} chunks from {file_path}")
        return chunks

    def _extract_node(
        self,
        node,
        code: str,
        capture_name: str,
        language: str,
        file_path: str,
    ) -> Chunk | None:
        """
        Extract a tree-sitter node as a chunk.

        Args:
            node: Tree-sitter node
            code: Source code
            capture_name: Capture name (e.g., "function.def")
            language: Programming language
            file_path: Path to file

        Returns:
            Chunk or None
        """
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1

        # Extract text
        text = code[node.start_byte : node.end_byte]

        # Parse capture name to determine type
        chunk_type = capture_name.split(".")[0]

        # Extract name from child nodes
        name = self._extract_name(node, language)

        # Determine chunk categorization
        if chunk_type in ("function", "method"):
            return Chunk(
                text=text,
                start_line=start_line,
                end_line=end_line,
                chunk_type="function" if chunk_type == "function" else "method",
                function_name=name,
                language=language,
                file_path=file_path,
            )
        elif chunk_type in ("class", "struct", "interface", "trait"):
            return Chunk(
                text=text,
                start_line=start_line,
                end_line=end_line,
                chunk_type="class",
                class_name=name,
                language=language,
                file_path=file_path,
            )
        elif chunk_type == "impl":
            # Rust impl blocks
            return Chunk(
                text=text,
                start_line=start_line,
                end_line=end_line,
                chunk_type="class",
                class_name="impl",
                language=language,
                file_path=file_path,
            )
        elif chunk_type == "type":
            # Go type declarations
            return Chunk(
                text=text,
                start_line=start_line,
                end_line=end_line,
                chunk_type="class",
                class_name=name,
                language=language,
                file_path=file_path,
            )
        else:
            # Generic chunk
            return Chunk(
                text=text,
                start_line=start_line,
                end_line=end_line,
                chunk_type="text",
                language=language,
                file_path=file_path,
            )

    def _extract_name(self, node, language: str) -> str | None:
        """
        Extract name from tree-sitter node.

        Args:
            node: Tree-sitter node
            language: Programming language

        Returns:
            Name string or None
        """
        # Try to find name in child nodes
        for child in node.children:
            if child.type in (
                "identifier",
                "type_identifier",
                "field_identifier",
                "property_identifier",
            ):
                return child.text.decode("utf8")

        # Fallback: try named children
        for child in node.named_children:
            if child.type in (
                "identifier",
                "type_identifier",
                "field_identifier",
                "property_identifier",
            ):
                return child.text.decode("utf8")

        return None
