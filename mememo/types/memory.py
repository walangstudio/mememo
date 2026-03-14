"""
Memory type definitions using Pydantic models.

Defines all data structures for memories, git context, and query parameters.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# Git context types
class RepoContext(BaseModel):
    """Repository context information."""

    id: str = Field(description="SHA-256 hash of repo path")
    name: str = Field(description="Repository name")
    path: str = Field(description="Absolute path to repository")
    remote_url: str | None = Field(None, description="Git remote URL (if available)")


class BranchContext(BaseModel):
    """Branch context information."""

    name: str = Field(description="Branch name")
    commit_hash: str = Field(description="Current commit SHA")


class GitContext(BaseModel):
    """Complete git context for a memory."""

    repo: RepoContext
    branch: BranchContext


# Memory content types
MemoryContentType = Literal[
    "code_snippet",
    "context",
    "summary",
    "relationship",
    "decision",
    "analysis",
    "conversation",
]

# Types tied to source files — staled when the file changes in a commit
CODE_MEMORY_TYPES: frozenset[str] = frozenset({"code_snippet", "relationship"})

# Types that survive code changes — decisions, analysis, and conversation notes persist
PERSISTENT_MEMORY_TYPES: frozenset[str] = frozenset(
    {"context", "summary", "decision", "analysis", "conversation"}
)


class MemoryContent(BaseModel):
    """Content of a memory with optional code-aware metadata."""

    type: MemoryContentType
    text: str = Field(description="The actual content text")
    language: str | None = Field(None, description="Programming language (if code)")
    file_path: str | None = Field(None, description="Relative path to file")
    line_range: tuple[int, int] | None = Field(None, description="Start and end line numbers")

    # NEW in v0.3.0: Code-aware metadata
    function_name: str | None = Field(None, description="Function name (if code chunk)")
    class_name: str | None = Field(None, description="Class name (if code chunk)")
    docstring: str | None = Field(None, description="Docstring/comment")
    decorators: list[str] | None = Field(None, description="Decorators/annotations")
    parent_class: str | None = Field(None, description="Parent class for methods")


class MemoryMetadata(BaseModel):
    """Metadata about a memory."""

    tags: list[str] = Field(default_factory=list, description="User-defined tags")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    checksum: str = Field(description="SHA-256 hash of content")
    token_count: int = Field(ge=0, description="Number of tokens in content")
    embedding_shard: int | None = Field(None, description="FAISS shard number")
    embedding_index: int | None = Field(None, description="Index within shard")
    stale: bool = Field(
        default=False, description="Source file changed since this memory was created"
    )
    stale_reason: str | None = Field(None, description="Why this memory was marked stale")


class MemoryRelationships(BaseModel):
    """Relationships between memories."""

    depends_on: list[str] | None = Field(None, description="Memory IDs this depends on")
    related_to: list[str] | None = Field(None, description="Related memory IDs")


class MemorySummary(BaseModel):
    """Auto-generated summaries of memory content."""

    one_line: str = Field(description="One-line summary")
    detailed: str | None = Field(None, description="Detailed summary")


class Memory(BaseModel):
    """Complete memory object."""

    id: str = Field(description="UUID v4")
    repo: RepoContext
    branch: BranchContext
    content: MemoryContent
    metadata: MemoryMetadata
    relationships: MemoryRelationships
    summary: MemorySummary

    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})


# Creation and query parameters
class CreateMemoryParams(BaseModel):
    """Parameters for creating a new memory."""

    content: str = Field(description="Content to store")
    type: MemoryContentType = Field(default="context", description="Type of memory")
    file_path: str | None = Field(None, description="Relative file path")
    line_range: tuple[int, int] | None = Field(None, description="Line range")
    language: str | None = Field(None, description="Programming language")
    tags: list[str] | None = Field(None, description="Tags")
    relationships: MemoryRelationships | None = Field(None, description="Relationships")

    # NEW in v0.3.0: Code-aware fields (auto-populated by chunker)
    function_name: str | None = None
    class_name: str | None = None
    docstring: str | None = None
    decorators: list[str] | None = None
    parent_class: str | None = None


class MemoryFilters(BaseModel):
    """Filters for querying memories."""

    id: str | None = None
    repo_id: str | None = None
    branch: str | None = None
    file_path: str | None = None
    tags: list[str] | None = None
    type: MemoryContentType | None = None
    language: str | None = None
    function_name: str | None = None
    class_name: str | None = None
    cross_branch: bool = Field(default=False, description="Search across all branches")
    include_stale: bool = Field(default=False, description="Include stale memories in results")
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)
    sort_by: Literal["date", "file", "type"] = Field(default="date")


class SearchParams(BaseModel):
    """Parameters for semantic search."""

    query: str = Field(description="Search query")
    top_k: int = Field(default=5, ge=1, le=100, description="Number of results")
    type: MemoryContentType | None = Field(None, description="Filter by type")
    min_similarity: float = Field(
        default=0.7, ge=0.0, le=1.0, description="Minimum similarity threshold"
    )
    cross_branch: bool = Field(default=False, description="Search across all branches")
    repo_id: str | None = Field(None, description="Filter by repository")
    branch: str | None = Field(None, description="Filter by branch")
    include_stale: bool = Field(default=False, description="Include stale memories in results")


class SummarizeParams(BaseModel):
    """Parameters for summarizing memories."""

    memory_ids: list[str] | None = Field(None, description="Specific memory IDs")
    file_path: str | None = Field(None, description="Filter by file path")
    tags: list[str] | None = Field(None, description="Filter by tags")
    max_tokens: int = Field(default=1000, ge=100, le=10000, description="Max tokens in summary")


# Search results
class SearchResult(BaseModel):
    """Single search result with similarity score."""

    memory: Memory
    similarity: float = Field(ge=0.0, le=1.0, description="Similarity score")

    model_config = ConfigDict(json_encoders={datetime: lambda v: v.isoformat()})
