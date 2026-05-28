"""
Memory type definitions using Pydantic models.

Defines all data structures for memories, git context, and query parameters.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


# Risk grading literal — populated by sync_commits / detect_changes (FR-007/008/009)
RiskGrade = Literal["WILL_BREAK", "LIKELY_AFFECTED", "MAY_NEED_TESTING"]

# Memory event operations — append-only log for time-travel (FR-003)
MemoryEventOp = Literal["CREATED", "UPDATED", "STALED", "DELETED", "RESTORED"]

# Sentinel SHA used when no git commit is available (non-repo, or pre-v0.4 backfill
# of rows that never had a commit_hash). Forty zeros — visually unmistakable in
# logs and event-replay queries, and DB-enforceable via CHECK constraints.
NULL_SHA: str = "0" * 40
BACKFILL_SHA: str = "b" + "a" * 38 + "1"  # 'baaa…1' — distinct sentinel for legacy seed


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

    # NEW in v0.4.0 — commit-aware foundation (FR-001, FR-002, FR-007)
    created_at_sha: str | None = Field(
        None, description="Commit SHA at which this memory was first created"
    )
    updated_at_sha: str | None = Field(
        None, description="Commit SHA at which this memory was last updated"
    )
    risk_grade: RiskGrade | None = Field(
        None,
        description="Set by detect_changes / sync_commits when the source has drifted "
        "from the memory's reference point",
    )


# Full git SHA-1 (40 hex chars) and short-prefix forms. Exported so tools /
# routes / hooks share one validation rule.
SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
SHA_PREFIX_PATTERN = re.compile(r"^[0-9a-fA-F]{4,40}$")
_SHA_PATTERN = SHA_PATTERN  # legacy alias used by the MemoryEvent validator below


def coerce_sha(value: str | None) -> str:
    """Normalize an optional commit hash into a known-safe SHA value.

    Returns the input when it matches a full 40-char hex SHA, otherwise
    the ``NULL_SHA`` sentinel. Centralises the "empty / non-hex / short /
    None" fallback dance every commit-aware write path was repeating.
    """
    if value and SHA_PATTERN.match(value):
        return value
    return NULL_SHA


# NEW in v0.4.0 — append-only event log for time-travel + branch merge (FR-003, FR-004)


class MemoryEvent(BaseModel):
    """A single mutation on a memory, identified by commit SHA + branch.

    State at a target SHA is reconstructed by replaying these events; full-copy
    snapshots are explicitly NOT stored (FR-005).
    """

    id: int | None = Field(None, description="SQLite rowid, populated on insert")
    commit_sha: str = Field(
        description="Git SHA-1 (40 lowercase hex) at which the event was emitted. "
        "Use NULL_SHA when no git context is available; never the empty string.",
    )
    memory_id: str = Field(description="Owning memory UUID")
    op: MemoryEventOp = Field(description="What happened to the memory at this commit")
    content_sha: str | None = Field(
        None, description="Checksum of the content blob this op references; null for STALED/DELETED"
    )
    branch: str = Field(description="Branch on which the event was emitted")
    ts: datetime = Field(default_factory=datetime.now, description="Event timestamp")

    @field_validator("commit_sha")
    @classmethod
    def _validate_commit_sha(cls, v: str) -> str:
        if not _SHA_PATTERN.match(v):
            raise ValueError(
                f"commit_sha must be a 40-char hex SHA (use NULL_SHA / BACKFILL_SHA "
                f"sentinels when no real commit is available); got {v!r}"
            )
        return v


# NEW in v0.4.0 — per-branch indexing state (FR-011)
class BranchState(BaseModel):
    """Tracks the last-indexed SHA and merge-base parent per (repo_id, branch)."""

    repo_id: str
    branch: str
    last_indexed_sha: str | None = None
    parent_sha: str | None = Field(
        None, description="Merge-base with default branch; used by merge_branch tool"
    )


# v0.5 (FR-017): typed edges in the memory graph. v0.7 adds DOCUMENTS;
# v0.8 adds REFERENCES (doc section -> external URL, stays unresolved).
# Keep in sync with chunking.base_chunker.EdgeType — together they are the
# source of truth for edge types (the relations.type DB CHECK was dropped).
RelationType = Literal[
    "IMPORTS",
    "CALLS",
    "EXTENDS",
    "IMPLEMENTS",
    "USES",
    "DECORATED_BY",
    "DOCUMENTS",
    "REFERENCES",
]
RelationConfidence = Literal["EXTRACTED", "INFERRED", "AMBIGUOUS"]


class Relation(BaseModel):
    """A persisted edge between memories.

    Either ``target_memory_id`` (when the resolver found a match) or
    ``target_symbol`` (when unresolved) is populated. ``confidence`` records
    how the edge was resolved.
    """

    id: str
    repo_id: str
    branch: str
    source_memory_id: str
    target_memory_id: str | None = None
    target_symbol: str | None = None
    type: RelationType
    confidence: RelationConfidence = "EXTRACTED"
    created_at_sha: str
    stale: bool = False
    community: int | None = None

    @field_validator("created_at_sha")
    @classmethod
    def _validate_sha(cls, v: str) -> str:
        if len(v) != 40:
            raise ValueError(f"created_at_sha must be 40 chars; got len={len(v)}")
        return v


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
    tags: list[str] | None = Field(None, description="Filter by tags (AND logic, all must match)")


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
