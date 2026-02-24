"""
Pydantic schemas for MCP tool parameters and responses.

All tool inputs/outputs are validated using these models.
"""

from typing import Literal

from pydantic import BaseModel, Field

from ..types.memory import (
    Memory,
    MemoryContentType,
)

# ============================================================================
# store_memory tool
# ============================================================================


class StoreMemoryParams(BaseModel):
    """Parameters for storing a new memory."""

    content: str = Field(description="Content to store in memory")
    type: MemoryContentType = Field(
        default="code_snippet",
        description="Type of memory: code_snippet, context, summary, or relationship",
    )
    language: str | None = Field(
        default=None, description="Programming language (auto-detected if not provided)"
    )
    file_path: str | None = Field(default=None, description="File path for code snippets")
    line_range: tuple[int, int] | None = Field(
        default=None, description="Line range tuple (start, end)"
    )
    tags: list[str] | None = Field(default=None, description="Tags for categorization")

    # Code-aware metadata (optional - auto-extracted if not provided)
    function_name: str | None = Field(
        default=None, description="Function name (auto-extracted from code)"
    )
    class_name: str | None = Field(
        default=None, description="Class name (auto-extracted from code)"
    )
    docstring: str | None = Field(default=None, description="Docstring (auto-extracted from code)")
    decorators: list[str] | None = Field(
        default=None, description="Decorators (auto-extracted from code)"
    )
    parent_class: str | None = Field(
        default=None, description="Parent class (auto-extracted from code)"
    )


class StoreMemoryResponse(BaseModel):
    """Response from storing a memory."""

    success: bool = Field(description="Whether storage was successful")
    memory_id: str = Field(description="Unique ID of stored memory")
    message: str = Field(description="Success or error message")
    token_count: int = Field(description="Number of tokens in content")
    checksum: str = Field(description="Content checksum for deduplication")


# ============================================================================
# retrieve_memory tool
# ============================================================================


class RetrieveMemoryParams(BaseModel):
    """Parameters for retrieving a memory by ID."""

    memory_id: str = Field(description="Memory ID to retrieve")


class RetrieveMemoryResponse(BaseModel):
    """Response from retrieving a memory."""

    success: bool = Field(description="Whether retrieval was successful")
    memory: Memory | None = Field(default=None, description="Retrieved memory")
    message: str = Field(description="Success or error message")


# ============================================================================
# search_similar tool
# ============================================================================


class SearchSimilarParams(BaseModel):
    """Parameters for semantic similarity search."""

    query: str = Field(description="Search query text")
    top_k: int = Field(default=5, ge=1, le=50, description="Number of results to return (1-50)")
    min_similarity: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Minimum similarity threshold (0.0-1.0)",
    )
    type: MemoryContentType | None = Field(default=None, description="Filter by memory type")
    language: str | None = Field(default=None, description="Filter by programming language")


class SearchResult(BaseModel):
    """Single search result with similarity score."""

    memory: Memory = Field(description="Matched memory")
    similarity: float = Field(description="Similarity score (0.0-1.0)")


class SearchSimilarResponse(BaseModel):
    """Response from similarity search."""

    success: bool = Field(description="Whether search was successful")
    results: list[SearchResult] = Field(description="List of search results")
    message: str = Field(description="Success or error message")
    count: int = Field(description="Number of results returned")


# ============================================================================
# list_memories tool
# ============================================================================


class ListMemoriesParams(BaseModel):
    """Parameters for listing memories with filters."""

    type: MemoryContentType | None = Field(default=None, description="Filter by memory type")
    language: str | None = Field(default=None, description="Filter by programming language")
    tags: list[str] | None = Field(default=None, description="Filter by tags")
    file_path: str | None = Field(default=None, description="Filter by file path")
    function_name: str | None = Field(default=None, description="Filter by function name")
    class_name: str | None = Field(default=None, description="Filter by class name")
    limit: int = Field(default=50, ge=1, le=500, description="Max results (1-500)")


class ListMemoriesResponse(BaseModel):
    """Response from listing memories."""

    success: bool = Field(description="Whether listing was successful")
    memories: list[Memory] = Field(description="List of memories")
    message: str = Field(description="Success or error message")
    count: int = Field(description="Number of memories returned")
    total: int = Field(description="Total matching memories (before limit)")


# ============================================================================
# summarize_context tool
# ============================================================================


class SummarizeContextParams(BaseModel):
    """Parameters for summarizing memories."""

    memory_ids: list[str] = Field(description="List of memory IDs to summarize")
    max_tokens: int = Field(
        default=500, ge=100, le=2000, description="Max tokens in summary (100-2000)"
    )
    group_by: Literal["file", "type", "none"] = Field(
        default="file", description="How to group memories in summary"
    )


class SummarizeContextResponse(BaseModel):
    """Response from summarizing context."""

    success: bool = Field(description="Whether summarization was successful")
    summary: str = Field(description="Hierarchical summary text")
    message: str = Field(description="Success or error message")
    token_count: int = Field(description="Number of tokens in summary")
    memories_included: int = Field(description="Number of memories included")


# ============================================================================
# delete_memory tool
# ============================================================================


class DeleteMemoryParams(BaseModel):
    """Parameters for deleting a memory."""

    memory_id: str = Field(description="Memory ID to delete")
    confirm: bool = Field(default=False, description="Confirmation flag (must be True)")


class DeleteMemoryResponse(BaseModel):
    """Response from deleting a memory."""

    success: bool = Field(description="Whether deletion was successful")
    message: str = Field(description="Success or error message")
    memory_id: str = Field(description="ID of deleted memory")


# ============================================================================
# index_repository tool
# ============================================================================


class IndexRepositoryParams(BaseModel):
    """Parameters for indexing a repository."""

    repo_path: str = Field(description="Path to repository root")
    file_patterns: list[str] = Field(
        default=["**/*.py", "**/*.ts", "**/*.js", "**/*.go", "**/*.rs"],
        description="Glob patterns for files to index",
    )
    incremental: bool = Field(
        default=True, description="Use incremental indexing (only changed files)"
    )
    max_files: int = Field(default=1000, ge=1, le=10000, description="Max files to index (1-10000)")


class IndexRepositoryResponse(BaseModel):
    """Response from indexing a repository."""

    success: bool = Field(description="Whether indexing was successful")
    message: str = Field(description="Success or error message")
    files_indexed: int = Field(description="Number of files indexed")
    chunks_created: int = Field(description="Number of chunks created")
    files_skipped: int = Field(description="Number of files skipped (unchanged)")
    duration_seconds: float = Field(description="Indexing duration in seconds")


# ============================================================================
# check_memory tool
# ============================================================================


class CheckMemoryParams(BaseModel):
    """Parameters for checking memory statistics."""

    include_git_info: bool = Field(default=True, description="Include git repository info")


class CheckMemoryResponse(BaseModel):
    """Response from checking memory."""

    success: bool = Field(description="Whether check was successful")
    message: str = Field(description="Success or error message")
    statistics: dict = Field(description="Memory statistics")
    git_context: dict | None = Field(default=None, description="Current git context")


# ============================================================================
# refresh_memory tool
# ============================================================================


class RefreshMemoryParams(BaseModel):
    """Parameters for refreshing a memory."""

    memory_id: str = Field(description="Memory ID to refresh")
    new_content: str | None = Field(default=None, description="New content (if updating content)")
    tags: list[str] | None = Field(default=None, description="New tags (if updating tags)")


class RefreshMemoryResponse(BaseModel):
    """Response from refreshing a memory."""

    success: bool = Field(description="Whether refresh was successful")
    message: str = Field(description="Success or error message")
    memory: Memory | None = Field(default=None, description="Updated memory")
