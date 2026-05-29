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
import os
import threading
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path

from fastmcp import FastMCP

# v0.6 MCP resources (T031)
from . import resources as _resources
from .core.git_manager import GitManager
from .core.llm_adapter import LLMAdapter
from .core.memory_manager import MemoryManager
from .core.storage_manager import StorageManager
from .core.vector_index import VectorIndex
from .embeddings.embedder import Embedder
from .tools import (
    batch_store as batch_store_impl,
)
from .tools import (
    capture as capture_impl,
)
from .tools import (
    check_memory as check_memory_impl,
)
from .tools import (
    cleanup_memory as cleanup_memory_impl,
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
    manage_skill as manage_skill_impl,
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

# v0.6 Cypher subset query tool (T035)
from .tools.cypher_query import (
    CypherQueryParams,
    CypherQueryResponse,
)
from .tools.cypher_query import (
    cypher_query as cypher_query_impl,
)

# v0.4 commit-aware MCP tools (T007, T008, T010)
from .tools.detect_changes import (
    DetectChangesParams,
    DetectChangesResponse,
)
from .tools.detect_changes import (
    detect_changes as detect_changes_impl,
)
from .tools.graph_impact import (
    GraphImpactParams,
    GraphImpactResponse,
)
from .tools.graph_impact import (
    graph_impact as graph_impact_impl,
)

# v0.5 graph traversal tools (T023, T024)
from .tools.graph_neighbors import (
    GraphNeighborsParams,
    GraphNeighborsResponse,
)
from .tools.graph_neighbors import (
    graph_neighbors as graph_neighbors_impl,
)
from .tools.graph_path import (
    GraphPathParams,
    GraphPathResponse,
)
from .tools.graph_path import (
    graph_path as graph_path_impl,
)
from .tools.merge_branch import (
    MergeBranchParams,
    MergeBranchResponse,
)
from .tools.merge_branch import (
    merge_branch as merge_branch_impl,
)
from .tools.recall_at_commit import (
    RecallAtCommitParams,
    RecallAtCommitResponse,
)
from .tools.recall_at_commit import (
    recall_at_commit as recall_at_commit_impl,
)
from .tools.schemas import (
    BatchStoreParams,
    BatchStoreResponse,
    CaptureParams,
    CaptureResponse,
    CheckMemoryParams,
    CheckMemoryResponse,
    CleanupMemoryParams,
    CleanupMemoryResponse,
    DeleteMemoryParams,
    DeleteMemoryResponse,
    EndSessionParams,
    EndSessionResponse,
    IndexRepositoryParams,
    IndexRepositoryResponse,
    ListMemoriesParams,
    ListMemoriesResponse,
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

try:
    _VERSION = pkg_version("mememo")
except PackageNotFoundError:
    # Running from a source checkout without pip install -e .
    _VERSION = "0.0.0+local"

# Initialize logging. Stderr is safe for MCP stdio (only stdout carries the
# JSON-RPC protocol), but stderr is invisible once Claude Code has spawned the
# server, so also tee to a rotating file the user can inspect after a crash.
_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def _setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        from logging.handlers import RotatingFileHandler

        log_dir = Path.home() / ".mememo" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_dir / "server.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
            )
        )
    except Exception:  # logging must never block startup
        pass
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, handlers=handlers)


_setup_logging()
logger = logging.getLogger(__name__)


def _maybe_start_identity_migration(storage_manager) -> None:
    """Spawn a one-shot daemon thread to backfill repo_ids via the new resolver.

    Runs only when 'identity_migrated' is not set in schema_meta. Guarded by a
    try/except so any failure is logged and never crashes the server.
    FAISS dir moves are delegated to the Wave 1C helper; if that module is not
    yet available, the DB rows are still re-keyed and the move is skipped.
    """
    try:
        row = storage_manager.conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'identity_migrated'"
        ).fetchone()
        if row is not None:
            return
    except Exception as exc:
        logger.debug("identity migration guard check failed: %s", exc)
        return

    def _run():
        try:
            import asyncio

            from .core.git_manager import GitManager
            from .core.identity import resolve_project_id
            from .core.project_config import load_project_config

            _git = GitManager()

            def _resolver(repo_path, remote_url):
                loop = asyncio.new_event_loop()
                try:
                    remote = loop.run_until_complete(_git.get_remote_url(repo_path))
                finally:
                    loop.close()
                return resolve_project_id(
                    repo_path=repo_path,
                    remote_url=remote or remote_url,
                    project_config=load_project_config(repo_path),
                )

            manifest = storage_manager._backfill_reindex_identity(_resolver, dry_run=False)

            # Attach conn so move_faiss_dirs can clear embedding pointers on conflict.
            for entry in manifest:
                entry["conn"] = storage_manager.conn

            # Attempt FAISS dir moves via Wave 1C helper (lazy import — may not exist yet).
            try:
                from .commands.reindex import move_faiss_dirs

                move_faiss_dirs(
                    base_path=storage_manager.base_dir / "vector_index",
                    manifest=manifest,
                )
            except ImportError:
                logger.debug("identity migration: reindex helper unavailable, skipping FAISS moves")
            except Exception as exc:
                logger.warning("identity migration: FAISS dir move failed: %s", exc)

            moves = sum(1 for e in manifest if not e["skipped"] and e["old_id"] != e["new_id"])
            storage_manager.conn.execute(
                "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('identity_migrated', '1')"
            )
            storage_manager.conn.commit()
            logger.info("identity migration: %d repo(s) re-keyed", moves)
        except Exception as exc:
            logger.warning("identity migration: background thread failed: %s", exc)

    t = threading.Thread(target=_run, daemon=True, name="mememo-identity-migration")
    t.start()


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

    # Loud-fail surfaces for silent-fallback footguns flagged in the v0.6 review.
    if getattr(config.security, "enable_encryption", False):
        try:
            import pysqlcipher3  # noqa: F401
        except ImportError:
            logger.warning(
                "MEMEMO_ENABLE_ENCRYPTION=true but pysqlcipher3 is not installed. "
                "Database WILL NOT be encrypted. Install with "
                "`pip install 'mememo[encryption]'` and rebuild stock Python's "
                "sqlite3 against sqlcipher, or unset the flag to silence this warning."
            )

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
    from .core.identity import GLOBAL_REPO_ID

    try:
        git_context = await git_manager.detect_context()
        repo_id = git_context.repo.id
        branch = git_context.branch.name
        logger.info(f"Git context detected - Repository: {repo_id}, Branch: {branch}")
    except RuntimeError:
        # Not in a git repository. MEMEMO_REPO_ID (already checked inside
        # resolve_project_id when in a repo) still applies here: if set, use it
        # so the caller can pin a stable id without a git remote.
        repo_id = os.environ.get("MEMEMO_REPO_ID", "").strip() or GLOBAL_REPO_ID
        branch = "main"
        logger.warning(
            "Not in a git repository: using repo_id=%r. "
            "Memories will commingle across non-git mememo sessions unless "
            "MEMEMO_REPO_ID is set explicitly.",
            repo_id,
        )

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
    if llm_adapter.is_passthrough():
        logger.warning(
            "No LLM API key detected (ANTHROPIC_API_KEY / OPENAI_API_KEY / "
            "GOOGLE_API_KEY / OLLAMA_HOST). `capture` will return passthrough=True "
            "with a self-extract prompt instead of extracting memories. Set one of "
            "the keys above to enable LLM-driven capture."
        )
        mode = "passthrough"
    else:
        mode = llm_adapter._provider()
    logger.info("LLM adapter initialized (provider: %s)", mode)

    # Initialize skill store for smart context selection
    from .context.skill_store import SkillStore

    skill_store = SkillStore(base_dir=base_dir)
    logger.info("Skill store initialized (dir: %s)", base_dir / "skills")

    # portable identity (wave 0b): one-shot background migration to re-derive
    # repo_ids using the new resolver (remote-url hash instead of path hash).
    # Guard: only run when the 'identity_migrated' flag is absent. Non-blocking:
    # spawned as a daemon thread so it never delays the MCP handshake. Failure is
    # logged only — never crashes the server.
    _maybe_start_identity_migration(storage_manager)

    logger.info("mememo v%s initialized successfully", _VERSION)


# Threading lock (not asyncio.Lock) because hookd handler threads each spin a
# fresh event loop — an asyncio.Lock would only serialize within a single loop.
# Contention is rare (cold first init only); after that the fast-path skips the
# lock entirely thanks to the outer is-None check.
_init_lock = threading.Lock()


async def ensure_initialized():
    """Ensure mememo is initialized (lazy, cross-thread safe)."""
    global config, memory_manager
    if memory_manager is not None:
        return
    with _init_lock:
        # Double-check: another thread may have completed init while we waited.
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
async def batch_store(params: BatchStoreParams) -> BatchStoreResponse:
    """
    Store multiple memories in a single batch operation.

    Optimized for bulk ingestion: single git context detection, batch
    embedding generation, and batch vector indexing. Use when storing
    results from multiple parallel agents or bulk imports.
    """
    await ensure_initialized()
    _audit_log("batch_store")
    return await batch_store_impl(params, memory_manager)


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


# ---------- v0.4 commit-aware MCP tools (FR-007, FR-010, FR-012) -----------


@mcp.tool()
async def detect_changes(params: DetectChangesParams) -> DetectChangesResponse:
    """
    Map git diff between two refs to affected memories with risk grades.

    Returns a list of {memory_id, file_path, line_range, change_kind, risk_grade}
    where risk_grade is one of:
    - WILL_BREAK       — source file deleted or renamed
    - LIKELY_AFFECTED  — file modified and the memory has a line_range
    - MAY_NEED_TESTING — file modified but no line_range, or unclassified change

    Read-only: does NOT mark memories stale or persist risk_grade. Use
    sync_commits when you want the staleness side-effects.
    """
    await ensure_initialized()
    _audit_log("detect_changes")
    return await detect_changes_impl(params, memory_manager)


@mcp.tool()
async def recall_at_commit(params: RecallAtCommitParams) -> RecallAtCommitResponse:
    """
    Time-travel semantic search: recall memory state as-of a target commit SHA.

    Resolves the SHA to its commit timestamp, replays the append-only event
    log up to that timestamp to compute the alive memory set, then runs
    semantic search and filters results to that set.

    Use cases: "what did we know before this refactor?", auditing decisions
    against the code state when they were made.
    """
    await ensure_initialized()
    _audit_log("recall_at_commit")
    return await recall_at_commit_impl(params, memory_manager)


@mcp.tool()
async def graph_neighbors(params: GraphNeighborsParams) -> GraphNeighborsResponse:
    """
    Depth-limited BFS over typed edges in the memory graph (v0.5).

    Walks IMPORTS / CALLS / EXTENDS / USES / DECORATED_BY relations from
    the given memory, returning visited memory ids and the traversed edges.
    Use this when you need the local neighborhood of a function or class —
    callers, callees, base classes, etc.
    """
    await ensure_initialized()
    _audit_log("graph_neighbors")
    return await graph_neighbors_impl(params, memory_manager)


@mcp.tool()
async def graph_impact(params: GraphImpactParams) -> GraphImpactResponse:
    """
    Blast-radius reasoning over the memory graph (v0.5).

    BFS over relations from the named memory, filtered by confidence floor
    and edge type. Each reached memory is decorated with its current
    risk_grade (WILL_BREAK / LIKELY_AFFECTED / MAY_NEED_TESTING) if set —
    so you can ask "if I change THIS, what downstream code is already
    graded as at-risk?". direction='upstream' inverts the walk to find
    callers / dependents.
    """
    await ensure_initialized()
    _audit_log("graph_impact")
    return await graph_impact_impl(params, memory_manager)


@mcp.tool()
async def graph_path(params: GraphPathParams) -> GraphPathResponse:
    """
    Shortest directed edge path between two memories (v0.5).

    BFS over outbound relations. Returns the ordered list of memory_ids or
    null if no path exists within max_depth. Useful for impact reasoning:
    "does ServiceA reach DatabaseTable via any call chain?"
    """
    await ensure_initialized()
    _audit_log("graph_path")
    return await graph_path_impl(params, memory_manager)


# ---------- v0.6 MCP resources (FR-026, FR-027) ----------------------------


@mcp.resource("mememo://repo/{repo_id}/stats")
async def repo_stats_resource(repo_id: str) -> str:
    """Counts of memories, edges, communities, stale fraction, last-indexed SHA
    per branch. Payload capped at 4 KB."""
    await ensure_initialized()
    _audit_log("resource:repo_stats")
    return _resources.repo_stats(memory_manager, repo_id)


@mcp.resource("mememo://repo/{repo_id}/stale")
async def repo_stale_resource(repo_id: str) -> str:
    """Up to 50 most-recently-stale memories with their risk_grade and
    stale_reason. Use detect_changes for the full list."""
    await ensure_initialized()
    _audit_log("resource:repo_stale")
    return _resources.repo_stale(memory_manager, repo_id)


@mcp.resource("mememo://repo/{repo_id}/branch/{branch}/summary")
async def branch_summary_resource(repo_id: str, branch: str) -> str:
    """Per-branch counts of memories, relations, events; current
    last_indexed_sha + parent_sha."""
    await ensure_initialized()
    _audit_log("resource:branch_summary")
    return _resources.branch_summary(memory_manager, repo_id, branch)


@mcp.resource("mememo://repo/{repo_id}/community/{community_id}")
async def community_resource(repo_id: str, community_id: int) -> str:
    """Members of a community + top-degree nodes within it."""
    await ensure_initialized()
    _audit_log("resource:community")
    return _resources.community_summary(memory_manager, repo_id, int(community_id))


@mcp.tool()
async def cypher_query(params: CypherQueryParams) -> CypherQueryResponse:
    """
    Cypher subset query over the memory graph (v0.6).

    Supports a documented subset: ``MATCH (a)-[r:TYPE]->(b)`` single-hop
    patterns; ``WHERE`` with `=`, `<>`, `=~` (regex), `AND`, `OR`;
    ``RETURN ident.prop [AS alias]`` projections; ``LIMIT n``. Returns
    a structured error (error_kind="unsupported") naming the construct
    when the query uses anything else (WITH, MERGE/CREATE/DELETE,
    variable-length paths, aggregations, ...).
    """
    await ensure_initialized()
    _audit_log("cypher_query")
    return await cypher_query_impl(params, memory_manager)


@mcp.tool()
async def merge_branch(params: MergeBranchParams) -> MergeBranchResponse:
    """
    Union the source branch's alive memories into the target branch.

    Mirrors a git merge at the memory layer: dedupes by content checksum so
    you never get two copies of the same insight, and emits RESTORED events
    tagged at the merge SHA so event-replay can see the merge boundary.

    Typically called from the opt-in post-merge git hook installed via
    `mememo install-git-hooks`, but can be invoked manually after any merge.
    """
    await ensure_initialized()
    _audit_log("merge_branch")
    return await merge_branch_impl(params, memory_manager)


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
    # Start the hook sidecar so `mememo capture|inject|pre-tool --hook` calls
    # from Claude Code don't each spawn a fresh ~3s python. Opt-out via env
    # so tests / scripts can suppress it.
    if os.environ.get("MEMEMO_NO_HOOK_DAEMON") != "1":
        try:
            from . import hookd

            hookd.start(version=_VERSION)
        except Exception:
            # Daemon is an optimisation; never block MCP boot on a failure here.
            logger.exception("hookd: failed to start sidecar (continuing without it)")
    logger.info("mememo v%s ready, serving MCP on stdio", _VERSION)
    mcp.run()


if __name__ == "__main__":
    run()
