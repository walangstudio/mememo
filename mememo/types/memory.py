"""
Memory type definitions using Pydantic models.

Defines all data structures for memories, git context, and query parameters.
"""

from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field
from uuid import UUID


# Git context types
class RepoContext(BaseModel):
    """Repository context information."""

    id: str = Field(description="SHA-256 hash of repo path")
    name: str = Field(description="Repository name")
    path: str = Field(description="Absolute path to repository")
    remote_url: Optional[str] = Field(None, description="Git remote URL (if available)")


class BranchContext(BaseModel):
    """Branch context information."""

    name: str = Field(description="Branch name")
    commit_hash: str = Field(description="Current commit SHA")


class GitContext(BaseModel):
    """Complete git context for a memory."""

    repo: RepoContext
    branch: BranchContext


# Memory content types
MemoryContentType = Literal["code_snippet", "context", "summary", "relationship"]


class MemoryContent(BaseModel):
    """Content of a memory with optional code-aware metadata."""

    type: MemoryContentType
    text: str = Field(description="The actual content text")
    language: Optional[str] = Field(None, description="Programming language (if code)")
    file_path: Optional[str] = Field(None, description="Relative path to file")
    line_range: Optional[tuple[int, int]] = Field(None, description="Start and end line numbers")

    # NEW in v0.3.0: Code-aware metadata
    function_name: Optional[str] = Field(None, description="Function name (if code chunk)")
    class_name: Optional[str] = Field(None, description="Class name (if code chunk)")
    docstring: Optional[str] = Field(None, description="Docstring/comment")
    decorators: Optional[list[str]] = Field(None, description="Decorators/annotations")
    parent_class: Optional[str] = Field(None, description="Parent class for methods")


class MemoryMetadata(BaseModel):
    """Metadata about a memory."""

    tags: list[str] = Field(default_factory=list, description="User-defined tags")
    created_at: datetime = Field(default_factory=datetime.now, description="Creation timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    checksum: str = Field(description="SHA-256 hash of content")
    token_count: int = Field(ge=0, description="Number of tokens in content")
    embedding_shard: Optional[int] = Field(None, description="FAISS shard number")
    embedding_index: Optional[int] = Field(None, description="Index within shard")


class MemoryRelationships(BaseModel):
    """Relationships between memories."""

    depends_on: Optional[list[str]] = Field(None, description="Memory IDs this depends on")
    related_to: Optional[list[str]] = Field(None, description="Related memory IDs")


class MemorySummary(BaseModel):
    """Auto-generated summaries of memory content."""

    one_line: str = Field(description="One-line summary")
    detailed: Optional[str] = Field(None, description="Detailed summary")


class Memory(BaseModel):
    """Complete memory object."""

    id: str = Field(description="UUID v4")
    repo: RepoContext
    branch: BranchContext
    content: MemoryContent
    metadata: MemoryMetadata
    relationships: MemoryRelationships
    summary: MemorySummary

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


# Creation and query parameters
class CreateMemoryParams(BaseModel):
    """Parameters for creating a new memory."""

    content: str = Field(description="Content to store")
    type: MemoryContentType = Field(default="context", description="Type of memory")
    file_path: Optional[str] = Field(None, description="Relative file path")
    line_range: Optional[tuple[int, int]] = Field(None, description="Line range")
    language: Optional[str] = Field(None, description="Programming language")
    tags: Optional[list[str]] = Field(None, description="Tags")
    relationships: Optional[MemoryRelationships] = Field(None, description="Relationships")

    # NEW in v0.3.0: Code-aware fields (auto-populated by chunker)
    function_name: Optional[str] = None
    class_name: Optional[str] = None
    docstring: Optional[str] = None
    decorators: Optional[list[str]] = None
    parent_class: Optional[str] = None


class MemoryFilters(BaseModel):
    """Filters for querying memories."""

    id: Optional[str] = None
    repo_id: Optional[str] = None
    branch: Optional[str] = None
    file_path: Optional[str] = None
    tags: Optional[list[str]] = None
    type: Optional[MemoryContentType] = None
    cross_branch: bool = Field(default=False, description="Search across all branches")
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)
    sort_by: Literal["date", "file", "type"] = Field(default="date")


class SearchParams(BaseModel):
    """Parameters for semantic search."""

    query: str = Field(description="Search query")
    top_k: int = Field(default=5, ge=1, le=100, description="Number of results")
    type: Optional[MemoryContentType] = Field(None, description="Filter by type")
    min_similarity: float = Field(default=0.7, ge=0.0, le=1.0, description="Minimum similarity threshold")
    cross_branch: bool = Field(default=False, description="Search across all branches")
    repo_id: Optional[str] = Field(None, description="Filter by repository")
    branch: Optional[str] = Field(None, description="Filter by branch")


class SummarizeParams(BaseModel):
    """Parameters for summarizing memories."""

    memory_ids: Optional[list[str]] = Field(None, description="Specific memory IDs")
    file_path: Optional[str] = Field(None, description="Filter by file path")
    tags: Optional[list[str]] = Field(None, description="Filter by tags")
    max_tokens: int = Field(default=1000, ge=100, le=10000, description="Max tokens in summary")


# Search results
class SearchResult(BaseModel):
    """Single search result with similarity score."""

    memory: Memory
    similarity: float = Field(ge=0.0, le=1.0, description="Similarity score")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
