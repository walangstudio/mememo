"""
mememo - FastMCP Server

All-Python code-aware memory server with:
- Multi-language code parsing (Python, TypeScript, Go, Rust, Java, C/C++, C#)
- Git-aware branch isolation
- Semantic vector search (FAISS)
- Security-first (secrets detection)
- Hybrid storage (SQLite + JSON blobs)
"""

import json
import logging
import time
from importlib.metadata import version as pkg_version
from pathlib import Path

from fastmcp import FastMCP

from .core.git_manager import GitManager
from .core.llm_adapter import LLMAdapter
from .core.memory_manager import MemoryManager
from .core.storage_manager import StorageManager
from .core.vector_index import VectorIndex
from .embeddings.embedder import Embedder
from .tools import (
    capture as capture_impl,
)
from .tools import (
    check_memory as check_memory_impl,
)
from .tools import (
    delete_memory as delete_memory_impl,
)
from .tools import (
    end_session as end_session_impl,
)
from .tools import (
    index_repository as index_repository_impl,
)
from .tools import (
    list_memories as list_memories_impl,
)
from .tools import (
    recall_context as recall_context_impl,
)
from .tools import (
    recent_context as recent_context_impl,
)
from .tools import (
    refresh_memory as refresh_memory_impl,
)
from .tools import (
    retrieve_memory as retrieve_memory_impl,
)
from .tools import (
    search_similar as search_similar_impl,
)
from .tools import (
    store_decision as store_decision_impl,
)
from .tools import (
    store_memory as store_memory_impl,
)
from .tools import (
    summarize_context as summarize_context_impl,
)
from .tools import (
    sync_commits as sync_commits_impl,
)
from .tools import (
    manage_skill as manage_skill_impl,
)
from .tools import (
    cleanup_memory as cleanup_memory_impl,
)
from .tools.schemas import (
    CaptureParams,
    CaptureResponse,
    CheckMemoryParams,
    CheckMemoryResponse,
    DeleteMemoryParams,
    DeleteMemoryResponse,
    EndSessionParams,
    EndSessionResponse,
    IndexRepositoryParams,
    IndexRepositoryResponse,
    ListMemoriesParams,
    ListMemoriesResponse,
    CleanupMemoryParams,
    CleanupMemoryResponse,
    ManageSkillParams,
    ManageSkillResponse,
    RecallContextParams,
    RecallContextResponse,
    RecentContextParams,
    RecentContextResponse,
    RefreshMemoryParams,
    RefreshMemoryResponse,
    RetrieveMemoryParams,
    RetrieveMemoryResponse,
    SearchSimilarParams,
    SearchSimilarResponse,
    StoreDecisionParams,
    StoreDecisionResponse,
    StoreMemoryParams,
    StoreMemoryResponse,
    SummarizeContextParams,
    SummarizeContextResponse,
    SyncCommitsParams,
    SyncCommitsResponse,
)
from .types.config import MemoConfig

_VERSION = pkg_version("mememo")

# Initialize logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Initialize FastMCP server
mcp = FastMCP("mememo", version=_VERSION)

# Global state (initialized on startup)
config: MemoConfig | None = None
memory_manager: MemoryManager | None = None
llm_adapter: LLMAdapter | None = None
skill_store = None  # Initialized lazily in initialize_mememo


@mcp.resource("config://mememo")
async def get_config() -> str:
    """
    Get current mememo configuration.

    Returns configuration as formatted text.
    """
    await ensure_initialized()

    return f"""mememo Configuration:

Storage:
  Base directory: {config.storage.base_dir}

Embedding:
  Model: {config.embedding.model_name}
  Device: {config.embedding.device}
  Batch size: {config.embedding.batch_size}

Security:
  Secrets detection: {config.security.secrets_detection}
  Auto-sanitize: {config.security.auto_sanitize}

Chunking:
  Max tokens: {config.chunking.max_tokens}
  Overlap tokens: {config.chunking.overlap_tokens}
  Preserve structure: {config.chunking.preserve_structure}

Search:
  Default top-k: {config.search.top_k}
  Min similarity: {config.search.min_similarity}
"""


@mcp.resource("stats://mememo")
async def get_statistics() -> str:
    """
    Get mememo statistics and metrics.

    Returns statistics as formatted text.
    """
    await ensure_initialized()

    stats = memory_manager.get_statistics()

    return f"""mememo Statistics:

Storage:
  Total memories: {stats.get('total_memories', 0)}
  Total size: {stats.get('total_size_mb', 0):.2f} MB

Vector Index:
  Total vectors: {stats.get('vector_index', {}).get('total_vectors', 0)}
  Active shards: {stats.get('vector_index', {}).get('active_shards', 0)}
  Total shards: {stats.get('vector_index', {}).get('total_shards', 0)}

Embedder:
  Model: {stats.get('embedder', {}).get('model_name', 'unknown')}
  Dimension: {stats.get('embedder', {}).get('dimension', 0)}
  Device: {stats.get('embedder', {}).get('device', 'unknown')}
"""


async def initialize_mememo():
    """
    Initialize mememo components.

    Loads configuration and initializes:
    - Storage manager
    - Git manager
    - Embedder
    - Vector index
    - Memory manager
    - LLM adapter (for capture tool)
    """
    global config, memory_manager, llm_adapter, skill_store

    logger.info("=" * 60)
    logger.info("Initializing mememo v%s", _VERSION)
    logger.info("=" * 60)

    # Load configuration from environment
    config = MemoConfig.from_env()

    # Ensure base directory exists
    base_dir = Path(config.storage.base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Storage directory: {base_dir}")
    logger.info(f"Embedding model: {config.embedding.model_name}")
    logger.info(f"Device: {config.embedding.device}")

    # Initialize storage manager
    storage_manager = StorageManager(base_dir=base_dir)
    logger.info("Storage manager initialized")

    # Initialize git manager
    git_manager = GitManager()
    logger.info("Git manager initialized")

    # Initialize embedder (lazy loading - model loaded on first use)
    embedder = Embedder(
        model_name=config.embedding.model_name,
        device=config.embedding.device,
        batch_size=config.embedding.batch_size,
    )
    logger.info(f"Embedder initialized: {config.embedding.model_name}")

    # Detect git context (optional - use defaults if not in a repo)
    try:
        git_context = await git_manager.detect_context()
        repo_id = git_context.repo.id
        branch = git_context.branch.name
        logger.info(f"Git context detected - Repository: {repo_id}, Branch: {branch}")
    except RuntimeError:
        # Not in a git repository - use defaults
        repo_id = "default"
        branch = "main"
        logger.info("Not in a git repository - using default repo/branch")

    # Initialize vector index
    vector_index = VectorIndex(
        base_path=base_dir / "vector_index",
        repo_id=repo_id,
        branch=branch,
        dimension=embedder.dimension,
    )
    logger.info(f"Vector index initialized (repo: {repo_id}, branch: {branch})")

    # Initialize memory manager
    memory_manager = MemoryManager(
        git_manager=git_manager,
        storage_manager=storage_manager,
        embedder=embedder,
        vector_index=vector_index,
        auto_sanitize=config.security.auto_sanitize,
        secrets_detection=config.security.secrets_detection,
    )
    logger.info("Memory manager initialized")

    # Initialize LLM adapter (lazy — no API calls until capture is invoked)
    llm_adapter = LLMAdapter()
    mode = "passthrough" if llm_adapter.is_passthrough() else llm_adapter._provider()
    logger.info("LLM adapter initialized (provider: %s)", mode)

    # Initialize skill store for smart context selection
    from .context.skill_store import SkillStore

    skill_store = SkillStore(base_dir=base_dir)
    logger.info("Skill store initialized (dir: %s)", base_dir / "skills")

    logger.info("mememo v%s initialized successfully", _VERSION)


async def ensure_initialized():
    """Ensure mememo is initialized (lazy initialization)."""
    global config, memory_manager
    if memory_manager is None:
        await initialize_mememo()


def _audit_log(tool: str) -> None:
    """Append one JSON line to audit.jsonl when audit logging is enabled."""
    if not config or not config.security.enable_audit_log:
        return
    try:
        audit_path = Path(config.storage.base_dir) / "audit.jsonl"
        vi = memory_manager.vector_index if memory_manager else None
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tool": tool,
            "repo": vi.repo_id if vi else None,
            "branch": vi.branch if vi else None,
        }
        with open(audit_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.debug(f"Audit log write failed: {e}")


# ============================================================================
# MCP Tools
# ============================================================================


@mcp.tool()
async def store_memory(params: StoreMemoryParams) -> StoreMemoryResponse:
    """
    Store code snippets, context, or summaries in local memory.

    Automatically:
    - Detects git context (repo + branch)
    - Checks for secrets (with optional sanitization)
    - Generates embedding
    - Indexes in vector database
    - Extracts code-aware metadata (functions, classes, docstrings)

    Examples:
        - Store Python function with auto-extraction
        - Store context notes with tags
        - Store code summary with relationships
    """
    await ensure_initialized()
    _audit_log("store_memory")
    return await store_memory_impl(params, memory_manager)


@mcp.tool()
async def capture(params: CaptureParams) -> CaptureResponse:
    """
    Passive memory capture — extract and store memorable facts from raw text.

    Pass any text (conversation snippet, session notes, observations). The
    configured LLM extracts decisions, context, analysis, and other facts and
    stores them automatically as the appropriate memory types.

    Passthrough mode (default, no LLM configured): returns passthrough=True and
    a passthrough_prompt you can use to self-extract by calling store_memory.

    Configure a provider in mememo/config/providers.yaml or set
    MEMEMO_LLM_CONFIG to a custom providers.yaml path.
    """
    await ensure_initialized()
    _audit_log("capture")
    return await capture_impl(params, memory_manager, llm_adapter)


@mcp.tool()
async def retrieve_memory(params: RetrieveMemoryParams) -> RetrieveMemoryResponse:
    """
    Retrieve a memory by its ID.

    Returns full memory with:
    - Content (text, language, file path, line range)
    - Metadata (tags, created/updated timestamps, token count)
    - Code-aware metadata (function name, class name, docstring)
    - Git context (repo + branch)
    - Summary (one-line + detailed)
    """
    await ensure_initialized()
    _audit_log("retrieve_memory")
    return await retrieve_memory_impl(params, memory_manager)


@mcp.tool()
async def search_similar(params: SearchSimilarParams) -> SearchSimilarResponse:
    """
    Search for similar memories using semantic vector search.

    Uses:
    - Embedding-based similarity (cosine similarity)
    - Git-aware branch isolation
    - Optional filters (type, language)
    - Configurable similarity threshold

    Returns ranked results with similarity scores (0.0-1.0).
    """
    await ensure_initialized()
    _audit_log("search_similar")
    return await search_similar_impl(params, memory_manager)


@mcp.tool()
async def list_memories(params: ListMemoriesParams) -> ListMemoriesResponse:
    """
    List memories with filters.

    Filters:
    - Type (code_snippet, context, summary, relationship, decision, analysis, conversation)
    - Language (python, typescript, javascript, go, rust, etc.)
    - Tags (user-defined tags)
    - File path, function name, class name
    - Git context (automatic branch isolation)
    - include_stale: include memories whose source file has changed (default: false)

    Returns matching memories (up to limit).
    """
    await ensure_initialized()
    _audit_log("list_memories")
    return await list_memories_impl(params, memory_manager)


@mcp.tool()
async def summarize_context(params: SummarizeContextParams) -> SummarizeContextResponse:
    """
    Summarize multiple memories into a hierarchical summary.

    Creates:
    - Grouped summary (by file, type, or none)
    - Token-limited output
    - One-line summaries for each memory
    - Hierarchical structure for readability

    Useful for:
    - Providing context to LLM prompts
    - Understanding memory clusters
    - Debugging memory storage
    """
    await ensure_initialized()
    _audit_log("summarize_context")
    return await summarize_context_impl(params, memory_manager)


@mcp.tool()
async def delete_memory(params: DeleteMemoryParams) -> DeleteMemoryResponse:
    """
    Delete a memory by ID.

    Requires confirmation (confirm=True) to prevent accidental deletions.

    Deletes:
    - Memory metadata (SQLite)
    - Memory content (JSON blob)
    - Vector index entry

    Note: Deletion is permanent and cannot be undone.
    """
    await ensure_initialized()
    _audit_log("delete_memory")
    return await delete_memory_impl(params, memory_manager)


@mcp.tool()
async def index_repository(params: IndexRepositoryParams) -> IndexRepositoryResponse:
    """
    Index a repository with code-aware chunking.

    Features:
    - Multi-language support (Python, TypeScript, Go, Rust, Java, C/C++, C#)
    - AST-based parsing for functions, classes, methods
    - Incremental indexing (only changed files)
    - Batch embedding generation
    - Progress tracking

    Supports glob patterns:
    - "**/*.py" - All Python files
    - "**/*.ts" - All TypeScript files
    - "src/**/*.go" - Go files in src directory

    Returns:
    - Files indexed count
    - Chunks created count
    - Duration in seconds
    """
    await ensure_initialized()
    _audit_log("index_repository")
    # Force full re-index if last snapshot is older than auto_reindex_age_minutes
    if params.incremental and config.indexing.enable_incremental:
        hashes_file = Path(config.storage.base_dir) / "merkle" / "file_hashes.json"
        if hashes_file.exists():
            age_minutes = (time.time() - hashes_file.stat().st_mtime) / 60
            if age_minutes > config.indexing.auto_reindex_age_minutes:
                logger.info(
                    f"Snapshot age {age_minutes:.1f}m > threshold "
                    f"{config.indexing.auto_reindex_age_minutes}m, forcing full re-index"
                )
                params = params.model_copy(update={"incremental": False})
    return await index_repository_impl(
        params, memory_manager, ignored_dirs=config.indexing.ignored_dirs
    )


@mcp.tool()
async def check_memory(params: CheckMemoryParams) -> CheckMemoryResponse:
    """
    Get memory statistics and health info.

    Returns:
    - Total memories count
    - Storage size (MB)
    - Vector index stats (vectors, shards)
    - Embedder info (model, dimension, device)
    - Git context (optional)

    Useful for:
    - Monitoring memory usage
    - Debugging indexing issues
    - Understanding current context
    """
    await ensure_initialized()
    _audit_log("check_memory")
    return await check_memory_impl(params, memory_manager)


@mcp.tool()
async def sync_commits(params: SyncCommitsParams) -> SyncCommitsResponse:
    """
    Patch memories to reflect new commits since the last index_repository run.

    For every file changed between the last indexed commit and HEAD:
    - Marks existing code_snippet/relationship memories as stale
    - Re-indexes files that still exist (creating fresh memories)

    Persistent memory types (decision, analysis, conversation, context, summary)
    are never staled — they survive code changes by design.

    Run after index_repository whenever new commits land. Faster than a full
    re-index because only changed files are processed.
    """
    await ensure_initialized()
    _audit_log("sync_commits")
    return await sync_commits_impl(params, memory_manager)


@mcp.tool()
async def refresh_memory(params: RefreshMemoryParams) -> RefreshMemoryResponse:
    """
    Update an existing memory.

    Can update:
    - Content (re-generates embedding)
    - Tags

    Preserves:
    - Memory ID (if tags-only update)
    - Git context
    - Code-aware metadata
    - Timestamps (updated_at refreshed)

    Note: Content updates create a new memory ID.
    """
    await ensure_initialized()
    _audit_log("refresh_memory")
    return await refresh_memory_impl(params, memory_manager)


@mcp.tool()
async def store_decision(params: StoreDecisionParams) -> StoreDecisionResponse:
    """
    Store a structured architectural decision.

    Assembles canonical markdown from structured fields:
    - Problem statement
    - Alternatives considered
    - Chosen option with rationale
    - Outcome (optional)

    Stored as a persistent 'decision' memory — never staled by code changes.
    """
    await ensure_initialized()
    _audit_log("store_decision")
    return await store_decision_impl(params, memory_manager)


@mcp.tool()
async def end_session(params: EndSessionParams) -> EndSessionResponse:
    """
    Store a session summary as a persistent conversation memory.

    Automatically prepends:
    - ISO timestamp (UTC)
    - Current git branch name

    Use at the end of a working session to capture what was accomplished.
    Stored as a 'conversation' memory — never staled by code changes.
    """
    await ensure_initialized()
    _audit_log("end_session")
    return await end_session_impl(params, memory_manager)


@mcp.tool()
async def recall_context(params: RecallContextParams) -> RecallContextResponse:
    """
    Semantic search across persistent memory types only.

    Searches: decision, analysis, context, conversation.
    Excludes: code_snippet, relationship (code-bound types).

    Uses a lower default similarity threshold (0.2) for broader recall.
    """
    await ensure_initialized()
    _audit_log("recall_context")
    return await recall_context_impl(params, memory_manager)


@mcp.tool()
async def recent_context(params: RecentContextParams) -> RecentContextResponse:
    """
    Return the N most recent memories, sorted by creation date.

    Pure SQL — no vector search. Useful for "what did I work on recently?"
    Optionally filter by memory type.
    """
    await ensure_initialized()
    _audit_log("recent_context")
    return await recent_context_impl(params, memory_manager)


@mcp.tool()
async def manage_skill(params: ManageSkillParams) -> ManageSkillResponse:
    """
    Manage reusable skill prompt templates for smart context injection.

    Skills are intent-based prompt templates automatically injected before
    memory context when the user's message matches the skill's intent category.

    Actions:
    - create: Create a new skill (requires name, intent, prompt)
    - list: List all skills
    - get: Get a skill by name (returns full prompt)
    - delete: Delete a skill by name

    Intent categories: coding, debugging, architecture, testing, review, general.
    """
    await ensure_initialized()
    _audit_log("manage_skill")
    return await manage_skill_impl(params, skill_store)


@mcp.tool()
async def cleanup_memory(params: CleanupMemoryParams) -> CleanupMemoryResponse:
    """
    Manual, controlled memory cleanup.

    Unlike auto-expiry, this tool gives you full control over what gets deleted.
    Default is dry_run=True (preview only). Set dry_run=False to actually delete.

    Cleanup modes (can combine):
    - older_than_days: Delete memories older than N days (optionally filtered by type)
    - stale_only: Delete code memories whose source file has changed
    - dedup: Remove exact-duplicate memories (same content checksum)

    Always preview with dry_run=True first before deleting.
    """
    await ensure_initialized()
    _audit_log("cleanup_memory")
    return await cleanup_memory_impl(params, memory_manager)


def run():
    """Run the FastMCP server."""
    mcp.run()


if __name__ == "__main__":
    run()
