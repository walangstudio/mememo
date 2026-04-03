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
        description=(
            "Type: code_snippet (file-bound, staled on change), context, summary, relationship, "
            "decision (architectural choices + rationale), analysis (bug/code investigation), "
            "conversation (AI session summaries). decision/analysis/conversation are persistent "
            "and never staled by code changes."
        ),
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
    repo_path: str | None = Field(
        default=None, description="Repository path (overrides cwd-based git detection)"
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
    repo_path: str | None = Field(
        default=None, description="Repository path (overrides cwd-based git detection)"
    )


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
    include_stale: bool = Field(
        default=False,
        description="Include stale memories (source changed since indexing)",
    )
    tags: list[str] | None = Field(
        default=None, description="Filter by tags (AND logic, all must match)"
    )
    repo_path: str | None = Field(
        default=None, description="Repository path (overrides cwd-based git detection)"
    )


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
    include_stale: bool = Field(
        default=False,
        description="Include stale memories (source changed since indexing)",
    )
    limit: int = Field(default=50, ge=1, le=500, description="Max results (1-500)")
    repo_path: str | None = Field(
        default=None, description="Repository path (overrides cwd-based git detection)"
    )


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
    """Parameters for summarizing memories or raw text."""

    memory_ids: list[str] | None = Field(
        default=None, description="List of memory IDs to summarize"
    )
    text: str | None = Field(
        default=None, description="Raw text to summarize directly (bypasses memory lookup)"
    )
    max_tokens: int = Field(
        default=500, ge=100, le=2000, description="Max tokens in summary (100-2000)"
    )
    group_by: Literal["file", "type", "none"] = Field(
        default="file", description="How to group memories in summary"
    )
    save_as_memory: bool = Field(
        default=False, description="If True, persist the summary as a 'summary' memory"
    )
    repo_path: str | None = Field(
        default=None, description="Repository path (overrides cwd-based git detection)"
    )


class SummarizeContextResponse(BaseModel):
    """Response from summarizing context."""

    success: bool = Field(description="Whether summarization was successful")
    summary: str = Field(description="Hierarchical summary text")
    message: str = Field(description="Success or error message")
    token_count: int = Field(description="Number of tokens in summary")
    memories_included: int = Field(description="Number of memories included")
    saved_memory_id: str | None = Field(
        default=None, description="ID of persisted summary memory (only when save_as_memory=True)"
    )


# ============================================================================
# delete_memory tool
# ============================================================================


class DeleteMemoryParams(BaseModel):
    """Parameters for deleting a memory."""

    memory_id: str = Field(description="Memory ID to delete")
    confirm: bool = Field(default=False, description="Confirmation flag (must be True)")
    repo_path: str | None = Field(
        default=None, description="Repository path (overrides cwd-based git detection)"
    )


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
    skip_reasons: dict[str, int] = Field(
        default_factory=dict,
        description="Breakdown of skip reasons (e.g. {'binary': 5, 'empty_chunks': 12, 'error': 1})",
    )
    duration_seconds: float = Field(description="Indexing duration in seconds")


# ============================================================================
# check_memory tool
# ============================================================================


class CheckMemoryParams(BaseModel):
    """Parameters for checking memory statistics."""

    include_git_info: bool = Field(default=True, description="Include git repository info")
    repo_path: str | None = Field(
        default=None, description="Repository path (overrides cwd-based git detection)"
    )


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
    repo_path: str | None = Field(
        default=None, description="Repository path (overrides cwd-based git detection)"
    )


class RefreshMemoryResponse(BaseModel):
    """Response from refreshing a memory."""

    success: bool = Field(description="Whether refresh was successful")
    message: str = Field(description="Success or error message")
    memory: Memory | None = Field(default=None, description="Updated memory")


# ============================================================================
# sync_commits tool
# ============================================================================


class SyncCommitsParams(BaseModel):
    """Parameters for syncing memories to new commits."""

    repo_path: str = Field(description="Path to the repository root")
    file_patterns: list[str] = Field(
        default=["**/*.py", "**/*.ts", "**/*.js", "**/*.go", "**/*.rs"],
        description="Glob patterns controlling which changed files get re-indexed",
    )


class SyncCommitsResponse(BaseModel):
    """Response from sync_commits."""

    success: bool = Field(description="Whether sync was successful")
    message: str = Field(description="Summary message")
    from_commit: str = Field(default="", description="Previously indexed commit (short SHA)")
    to_commit: str = Field(default="", description="Current HEAD commit (short SHA)")
    files_updated: int = Field(default=0, description="Changed files re-indexed")
    files_removed: int = Field(default=0, description="Files deleted since last index")
    memories_staled: int = Field(default=0, description="Code memories marked stale")
    chunks_created: int = Field(default=0, description="New chunks stored")
    duration_seconds: float = Field(default=0.0, description="Elapsed seconds")


# ============================================================================
# store_decision tool
# ============================================================================


class StoreDecisionParams(BaseModel):
    """Parameters for storing a structured architectural decision."""

    problem: str = Field(description="Problem or question being decided")
    alternatives: list[str] = Field(description="Options that were considered")
    chosen: str = Field(description="The chosen alternative")
    rationale: str = Field(description="Why this alternative was chosen")
    outcome: str | None = Field(default=None, description="Result or follow-up (if known)")
    tags: list[str] | None = Field(default=None, description="Tags for categorization")
    repo_path: str | None = Field(
        default=None, description="Repository path (overrides cwd-based git detection)"
    )


class StoreDecisionResponse(BaseModel):
    """Response from storing a decision."""

    success: bool = Field(description="Whether storage was successful")
    memory_id: str = Field(description="Unique ID of stored memory")
    message: str = Field(description="Success or error message")
    token_count: int = Field(description="Number of tokens in content")
    checksum: str = Field(description="Content checksum for deduplication")


# ============================================================================
# end_session tool
# ============================================================================


class EndSessionParams(BaseModel):
    """Parameters for storing a session summary."""

    summary: str = Field(description="Summary of what was accomplished this session")
    tags: list[str] | None = Field(default=None, description="Tags for categorization")
    repo_path: str | None = Field(
        default=None, description="Repository path (overrides cwd-based git detection)"
    )


class EndSessionResponse(BaseModel):
    """Response from storing a session summary."""

    success: bool = Field(description="Whether storage was successful")
    memory_id: str = Field(description="Unique ID of stored memory")
    message: str = Field(description="Success or error message")
    token_count: int = Field(description="Number of tokens in content")
    checksum: str = Field(description="Content checksum for deduplication")


# ============================================================================
# recall_context tool
# ============================================================================


class RecallContextParams(BaseModel):
    """Parameters for multi-type semantic search across persistent memory types."""

    query: str = Field(description="Search query text")
    top_k: int = Field(default=10, ge=1, le=50, description="Number of results to return (1-50)")
    min_similarity: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Minimum similarity threshold (0.0-1.0)",
    )
    tags: list[str] | None = Field(
        default=None, description="Filter by tags (AND logic, all must match)"
    )
    repo_path: str | None = Field(
        default=None, description="Repository path (overrides cwd-based git detection)"
    )


class RecallContextResponse(BaseModel):
    """Response from recall_context."""

    success: bool = Field(description="Whether search was successful")
    results: list[SearchResult] = Field(description="List of search results")
    message: str = Field(description="Success or error message")
    count: int = Field(description="Number of results returned")


# ============================================================================
# recent_context tool
# ============================================================================


class RecentContextParams(BaseModel):
    """Parameters for retrieving most recent memories."""

    limit: int = Field(default=10, ge=1, le=100, description="Number of recent memories to return")
    type: MemoryContentType | None = Field(default=None, description="Filter by memory type")
    repo_path: str | None = Field(
        default=None, description="Repository path (overrides cwd-based git detection)"
    )


class RecentContextResponse(BaseModel):
    """Response from recent_context."""

    success: bool = Field(description="Whether retrieval was successful")
    memories: list[Memory] = Field(description="List of recent memories")
    message: str = Field(description="Success or error message")
    count: int = Field(description="Number of memories returned")


# ============================================================================
# capture tool
# ============================================================================


class PreExtractedMemory(BaseModel):
    """A memory pre-extracted by the caller (skips LLM extraction)."""

    type: MemoryContentType = Field(description="Memory type")
    content: str = Field(description="Memory content")
    tags: list[str] | None = Field(default=None, description="Tags for categorization")


class ExtractedMemory(BaseModel):
    """Single memory extracted by the LLM from raw text."""

    type: MemoryContentType = Field(description="Extracted memory type")
    content: str = Field(description="Extracted memory content")
    tags: list[str] = Field(default_factory=list, description="Extracted tags")
    memory_id: str = Field(default="", description="Stored memory ID (empty if store failed)")


class CaptureParams(BaseModel):
    """Parameters for passive memory capture via LLM extraction."""

    text: str = Field(default="", description="Raw text to extract memories from (conversation, notes, etc.)")
    hint: str | None = Field(
        default=None,
        description="Optional hint to guide extraction (e.g. 'focus on decisions')",
    )
    pre_extracted: list[PreExtractedMemory] | None = Field(
        default=None,
        description="Pre-extracted memories to store directly (skips LLM extraction)",
    )
    repo_path: str | None = Field(
        default=None, description="Repository path (overrides cwd-based git detection)"
    )


class CaptureResponse(BaseModel):
    """Response from capture."""

    success: bool = Field(description="Whether capture completed (True even for passthrough)")
    extracted: list[ExtractedMemory] = Field(description="Memories extracted and stored")
    stored_count: int = Field(description="Number of memories successfully stored")
    message: str = Field(description="Summary message")
    passthrough: bool = Field(
        default=False,
        description="True when no LLM is configured — use passthrough_prompt to self-extract",
    )
    passthrough_prompt: str | None = Field(
        default=None,
        description="Extraction instructions for the calling model when passthrough=True",
    )


# ============================================================================
# manage_skill tool
# ============================================================================


class ManageSkillParams(BaseModel):
    """Parameters for managing skill prompt templates."""

    action: Literal["create", "list", "get", "delete"] = Field(
        description="Action to perform: create, list, get, or delete"
    )
    name: str | None = Field(
        default=None, description="Skill name (required for create/get/delete)"
    )
    intent: str | None = Field(
        default=None,
        description="Intent category: coding, debugging, architecture, testing, review, general",
    )
    prompt: str | None = Field(default=None, description="Skill prompt text (required for create)")
    priority: int | None = Field(default=None, description="Priority (higher = selected first)")
    tags: list[str] | None = Field(default=None, description="Tags for categorization")


class ManageSkillResponse(BaseModel):
    """Response from manage_skill."""

    success: bool = Field(description="Whether the operation was successful")
    message: str = Field(description="Success or error message")
    skills: list[dict] = Field(default_factory=list, description="Skill data")


# ============================================================================
# cleanup_memory tool
# ============================================================================


class CleanupMemoryParams(BaseModel):
    """Parameters for controlled memory cleanup."""

    older_than_days: int | None = Field(
        default=None, description="Delete memories older than N days"
    )
    type: MemoryContentType | None = Field(
        default=None, description="Only clean specific memory type"
    )
    dedup: bool = Field(
        default=False, description="Remove exact-duplicate memories (same content checksum)"
    )
    dedup_similarity: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description="Similarity threshold for dedup (unused when dedup uses checksum)",
    )
    stale_only: bool = Field(
        default=False, description="Only delete stale code memories (source file changed)"
    )
    dry_run: bool = Field(
        default=True, description="Preview what would be deleted without actually deleting"
    )


class CleanupMemoryResponse(BaseModel):
    """Response from cleanup_memory."""

    success: bool = Field(description="Whether cleanup was successful")
    message: str = Field(description="Summary message")
    candidates: list[dict] = Field(
        default_factory=list, description="Memories that were (or would be) deleted"
    )
    deleted_count: int = Field(default=0, description="Number of memories actually deleted")


# ============================================================================
# batch_store tool
# ============================================================================


class BatchStoreItemResult(BaseModel):
    """Result for a single item in a batch store operation."""

    memory_id: str = Field(default="", description="Stored memory ID (empty on failure)")
    success: bool = Field(description="Whether this item was stored")
    message: str = Field(default="", description="Error message on failure")


class BatchStoreParams(BaseModel):
    """Parameters for batch-storing multiple memories."""

    memories: list[StoreMemoryParams] = Field(description="List of memories to store")
    repo_path: str | None = Field(
        default=None, description="Repository path (overrides cwd-based git detection)"
    )


class BatchStoreResponse(BaseModel):
    """Response from batch store."""

    success: bool = Field(description="Whether the batch operation completed")
    results: list[BatchStoreItemResult] = Field(description="Per-item results")
    stored_count: int = Field(default=0, description="Number of memories stored")
    failed_count: int = Field(default=0, description="Number of failures")
