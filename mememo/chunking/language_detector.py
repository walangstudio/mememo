"""
Language detection for code-aware chunking.

Maps file extensions to programming languages.
"""

from pathlib import Path
from typing import Optional


# Comprehensive language mapping
LANGUAGE_MAP = {
    # Python
    ".py": "python",
    ".pyw": "python",
    ".pyx": "python",
    ".pyi": "python",

    # JavaScript
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",

    # JSX (React)
    ".jsx": "javascript",

    # TypeScript
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",

    # TSX (React)
    ".tsx": "typescript",

    # Go
    ".go": "go",

    # Rust
    ".rs": "rust",

    # C
    ".c": "c",
    ".h": "c",

    # C++
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c++": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".h++": "cpp",

    # C#
    ".cs": "csharp",

    # Java
    ".java": "java",

    # Kotlin
    ".kt": "kotlin",
    ".kts": "kotlin",

    # Scala
    ".scala": "scala",

    # Ruby
    ".rb": "ruby",

    # PHP
    ".php": "php",

    # Swift
    ".swift": "swift",

    # Svelte
    ".svelte": "svelte",

    # Vue
    ".vue": "vue",

    # Markdown
    ".md": "markdown",
    ".markdown": "markdown",
}


# Language categories for chunking strategy
LANGUAGE_CATEGORIES = {
    "python": {
        "category": "ast_supported",
        "chunker": "python_ast",
        "description": "Python with AST parsing",
    },
    "javascript": {
        "category": "tree_sitter",
        "chunker": "tree_sitter",
        "description": "JavaScript with tree-sitter",
    },
    "typescript": {
        "category": "tree_sitter",
        "chunker": "tree_sitter",
        "description": "TypeScript with tree-sitter",
    },
    "go": {
        "category": "tree_sitter",
        "chunker": "tree_sitter",
        "description": "Go with tree-sitter",
    },
    "rust": {
        "category": "tree_sitter",
        "chunker": "tree_sitter",
        "description": "Rust with tree-sitter",
    },
    "java": {
        "category": "tree_sitter",
        "chunker": "tree_sitter",
        "description": "Java with tree-sitter",
    },
    "c": {
        "category": "tree_sitter",
        "chunker": "tree_sitter",
        "description": "C with tree-sitter",
    },
    "cpp": {
        "category": "tree_sitter",
        "chunker": "tree_sitter",
        "description": "C++ with tree-sitter",
    },
    "csharp": {
        "category": "tree_sitter",
        "chunker": "tree_sitter",
        "description": "C# with tree-sitter",
    },
    "svelte": {
        "category": "tree_sitter",
        "chunker": "tree_sitter",
        "description": "Svelte with tree-sitter",
    },
}


def detect_language(file_path: str) -> Optional[str]:
    """
    Detect programming language from file extension.

    Args:
        file_path: Path to file

    Returns:
        Language name or None if not recognized
    """
    ext = Path(file_path).suffix.lower()
    return LANGUAGE_MAP.get(ext)


def get_chunker_type(language: str) -> str:
    """
    Get recommended chunker type for language.

    Args:
        language: Programming language

    Returns:
        Chunker type: "python_ast", "tree_sitter", or "text"
    """
    info = LANGUAGE_CATEGORIES.get(language)
    if info:
        return info["chunker"]
    return "text"  # Fallback to text chunking


def is_code_file(file_path: str) -> bool:
    """
    Check if file is a supported code file.

    Args:
        file_path: Path to file

    Returns:
        True if supported code file
    """
    return detect_language(file_path) is not None


def get_supported_languages() -> list[str]:
    """
    Get list of all supported languages.

    Returns:
        List of language names
    """
    return sorted(set(LANGUAGE_MAP.values()))


def get_supported_extensions() -> list[str]:
    """
    Get list of all supported file extensions.

    Returns:
        List of file extensions (including dot)
    """
    return sorted(LANGUAGE_MAP.keys())


def get_language_info(language: str) -> Optional[dict]:
    """
    Get detailed information about a language.

    Args:
        language: Programming language

    Returns:
        Dict with language info or None
    """
    return LANGUAGE_CATEGORIES.get(language)
