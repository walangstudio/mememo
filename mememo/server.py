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
from .core.llm_adapter import LLMAdapter
from .core.memory_manager import MemoryManager
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

# Comprehension tools: cited repo Q&A + architectural overview (passthrough-first)
from .tools.comprehension import (
    AskParams,
    AskResponse,
    OverviewParams,
    OverviewResponse,
)
from .tools.comprehension import (
    ask as ask_impl,
)
from .tools.comprehension import (
    overview as overview_impl,
)
from .tools.curate_skills import (
    curate_skills as curate_skills_impl,
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

# Phase 1 codebase diagram tool
from .tools.generate_diagram import (
    GenerateDiagramParams,
    GenerateDiagramResponse,
)
from .tools.generate_diagram import (
    generate_diagram as generate_diagram_impl,
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
    CurateSkillsParams,
    CurateSkillsResponse,
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

        class _SafeRotatingFileHandler(RotatingFileHandler):
            """Rotation that tolerates the log being held by another process.

            mememo runs as a long-lived MCP server AND as short-lived CLI/hook
            subprocesses, so two processes share server.log. On Windows the loser
            of a rollover race gets ``PermissionError`` (WinError 32). Swallow it
            and keep appending instead of spamming logging errors to stderr — the
            server process will rotate successfully on its next turn.
            """

            def doRollover(self):  # noqa: N802 - overrides stdlib RotatingFileHandler
                try:
                    super().doRollover()
                except OSError:
                    pass

        log_dir = Path.home() / ".mememo" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            _SafeRotatingFileHandler(
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

    base_dir = storage_manager.base_dir

    def _run():
        # Use a SEPARATE StorageManager (its own sqlite connection) so this
        # background thread never shares a connection/cursor with the live MCP
        # handler threads. WAL serialises the cross-connection writes safely.
        try:
            import asyncio

            from .core.git_manager import GitManager
            from .core.identity import resolve_project_id
            from .core.project_config import load_project_config
            from .core.storage_manager import StorageManager

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

            mig_storage = StorageManager(base_dir=base_dir)
            try:
                manifest = mig_storage._backfill_reindex_identity(_resolver, dry_run=False)

                # Attach this thread's conn so move_faiss_dirs can clear embedding
                # pointers on conflict — NOT the live server's connection.
                for entry in manifest:
                    entry["conn"] = mig_storage.conn

                try:
                    from .commands.reindex import move_faiss_dirs

                    move_faiss_dirs(
                        base_path=mig_storage.base_dir / "vector_index",
                        manifest=manifest,
                    )
                except ImportError:
                    logger.debug(
                        "identity migration: reindex helper unavailable, skipping FAISS moves"
                    )
                except Exception as exc:
                    logger.warning("identity migration: FAISS dir move failed: %s", exc)

                moves = sum(1 for e in manifest if not e["skipped"] and e["old_id"] != e["new_id"])
                mig_storage.conn.execute(
                    "INSERT OR REPLACE INTO schema_meta (key, value) VALUES ('identity_migrated', '1')"
                )
                mig_storage.conn.commit()
                logger.info("identity migration: %d repo(s) re-keyed", moves)
            finally:
                try:
                    mig_storage.conn.close()
                except Exception:
                    pass
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

    # Build the core stack via the shared factory (same wiring `mememo index`
    # uses, so the CLI and server never drift on vector-index path / security
    # flags / repo-id fallback). detect_context(None) uses the server's cwd.
    from .core.bootstrap import build_memory_manager

    memory_manager, repo_id, branch = await build_memory_manager(config)
    storage_manager = memory_manager.storage_manager
    logger.info("Memory manager initialized (repo: %s, branch: %s)", repo_id, branch)

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
    """Store a code snippet, context, summary, or decision in local memory.

    Auto-detects git context, scans for secrets, embeds, indexes, and extracts
    code metadata (function/class/docstring).
    """
    await ensure_initialized()
    _audit_log("store_memory")
    return await store_memory_impl(params, memory_manager)


@mcp.tool()
async def batch_store(params: BatchStoreParams) -> BatchStoreResponse:
    """Store many memories in one call: one git detection + batched embed/index.
    Use for parallel-agent results or bulk imports."""
    await ensure_initialized()
    _audit_log("batch_store")
    return await batch_store_impl(params, memory_manager)


@mcp.tool()
async def capture(params: CaptureParams) -> CaptureResponse:
    """Extract and store memorable facts (decisions, context, analysis) from raw text.

    Passthrough (default, no LLM): returns passthrough=True + a passthrough_prompt to
    self-extract via store_memory. Set MEMEMO_LLM_CONFIG to use a real provider.
    """
    await ensure_initialized()
    _audit_log("capture")
    return await capture_impl(params, memory_manager, llm_adapter)


@mcp.tool()
async def retrieve_memory(params: RetrieveMemoryParams) -> RetrieveMemoryResponse:
    """Retrieve a memory by ID (full content, metadata, code info, git context)."""
    await ensure_initialized()
    _audit_log("retrieve_memory")
    return await retrieve_memory_impl(params, memory_manager)


@mcp.tool()
async def search_similar(params: SearchSimilarParams) -> SearchSimilarResponse:
    """Semantic vector search over memories (branch-isolated, optional type/language
    filters). Returns ranked hits with similarity scores."""
    await ensure_initialized()
    _audit_log("search_similar")
    return await search_similar_impl(params, memory_manager)


@mcp.tool()
async def list_memories(params: ListMemoriesParams) -> ListMemoriesResponse:
    """List memories, optionally filtered by type/language/tags/file/function/class.
    Branch-isolated; excludes stale unless include_stale=true."""
    await ensure_initialized()
    _audit_log("list_memories")
    return await list_memories_impl(params, memory_manager)


@mcp.tool()
async def summarize_context(params: SummarizeContextParams) -> SummarizeContextResponse:
    """Summarize memories (or raw text) into a grouped, token-limited summary."""
    await ensure_initialized()
    _audit_log("summarize_context")
    return await summarize_context_impl(params, memory_manager)


@mcp.tool()
async def delete_memory(params: DeleteMemoryParams) -> DeleteMemoryResponse:
    """Delete a memory by ID. Permanent; requires confirm=True."""
    await ensure_initialized()
    _audit_log("delete_memory")
    return await delete_memory_impl(params, memory_manager)


@mcp.tool()
async def index_repository(params: IndexRepositoryParams) -> IndexRepositoryResponse:
    """Index a repo with AST-aware chunking (functions/classes/methods) across all
    supported languages. Incremental by default; accepts glob file_patterns."""
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
    """Memory stats/health: counts, storage size, vector-index + embedder info, git context."""
    await ensure_initialized()
    _audit_log("check_memory")
    return await check_memory_impl(params, memory_manager)


@mcp.tool()
async def sync_commits(params: SyncCommitsParams) -> SyncCommitsResponse:
    """Re-index files changed since the last index_repository (stale old code memories,
    create fresh ones). Persistent types (decision/analysis/conversation/context/summary)
    are never staled. Faster than a full re-index."""
    await ensure_initialized()
    _audit_log("sync_commits")
    return await sync_commits_impl(params, memory_manager)


# ---------- v0.4 commit-aware MCP tools (FR-007, FR-010, FR-012) -----------


@mcp.tool()
async def detect_changes(params: DetectChangesParams) -> DetectChangesResponse:
    """Map a git diff between two refs to affected memories, each graded
    WILL_BREAK / LIKELY_AFFECTED / MAY_NEED_TESTING. Read-only (no staling);
    use sync_commits for the staleness side-effects."""
    await ensure_initialized()
    _audit_log("detect_changes")
    return await detect_changes_impl(params, memory_manager)


@mcp.tool()
async def recall_at_commit(params: RecallAtCommitParams) -> RecallAtCommitResponse:
    """Time-travel recall: memory state as-of a commit SHA (replays the event log to the
    commit's timestamp, then semantic-searches that alive set)."""
    await ensure_initialized()
    _audit_log("recall_at_commit")
    return await recall_at_commit_impl(params, memory_manager)


@mcp.tool()
async def graph_neighbors(params: GraphNeighborsParams) -> GraphNeighborsResponse:
    """Depth-limited BFS over typed edges (IMPORTS/CALLS/EXTENDS/USES/DECORATED_BY) from a
    memory — its local neighborhood (callers, callees, base classes)."""
    await ensure_initialized()
    _audit_log("graph_neighbors")
    return await graph_neighbors_impl(params, memory_manager)


@mcp.tool()
async def graph_impact(params: GraphImpactParams) -> GraphImpactResponse:
    """Blast-radius BFS from a memory (confidence + edge-type filtered), decorating each
    reached node with its risk_grade (WILL_BREAK/LIKELY_AFFECTED/MAY_NEED_TESTING).
    direction='upstream' finds callers/dependents."""
    await ensure_initialized()
    _audit_log("graph_impact")
    return await graph_impact_impl(params, memory_manager)


@mcp.tool()
async def graph_path(params: GraphPathParams) -> GraphPathResponse:
    """Shortest directed edge path between two memories (BFS over outbound relations);
    null if none within max_depth."""
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
    """Cypher-subset query over the memory graph: single-hop MATCH (a)-[r:TYPE]->(b),
    WHERE (=, <>, =~, AND, OR), RETURN ident.prop [AS alias], LIMIT. Anything else
    returns a structured error_kind='unsupported'."""
    await ensure_initialized()
    _audit_log("cypher_query")
    return await cypher_query_impl(params, memory_manager)


@mcp.tool()
async def merge_branch(params: MergeBranchParams) -> MergeBranchResponse:
    """Union the source branch's alive memories into target (dedup by checksum),
    emitting RESTORED events at the merge SHA. Usually run by the post-merge git hook."""
    await ensure_initialized()
    _audit_log("merge_branch")
    return await merge_branch_impl(params, memory_manager)


@mcp.tool()
async def refresh_memory(params: RefreshMemoryParams) -> RefreshMemoryResponse:
    """Update a memory's content (re-embeds, new ID) or tags (same ID). Preserves git
    context + code metadata."""
    await ensure_initialized()
    _audit_log("refresh_memory")
    return await refresh_memory_impl(params, memory_manager)


@mcp.tool()
async def store_decision(params: StoreDecisionParams) -> StoreDecisionResponse:
    """Store a structured architectural decision (problem/alternatives/chosen/rationale)
    as a persistent 'decision' memory — never staled."""
    await ensure_initialized()
    _audit_log("store_decision")
    return await store_decision_impl(params, memory_manager)


@mcp.tool()
async def end_session(params: EndSessionParams) -> EndSessionResponse:
    """Store a session summary as a persistent 'conversation' memory (prepends UTC
    timestamp + branch). Never staled."""
    await ensure_initialized()
    _audit_log("end_session")
    return await end_session_impl(params, memory_manager)


@mcp.tool()
async def recall_context(params: RecallContextParams) -> RecallContextResponse:
    """Semantic search over persistent types only (decision/analysis/context/conversation),
    excluding code-bound types. Lower default threshold (0.2) for broad recall."""
    await ensure_initialized()
    _audit_log("recall_context")
    return await recall_context_impl(params, memory_manager)


@mcp.tool()
async def recent_context(params: RecentContextParams) -> RecentContextResponse:
    """The N most recent memories by creation date (pure SQL, no vector search).
    Optional type filter."""
    await ensure_initialized()
    _audit_log("recent_context")
    return await recent_context_impl(params, memory_manager)


@mcp.tool()
async def manage_skill(params: ManageSkillParams) -> ManageSkillResponse:
    """CRUD for reusable skill prompt templates (create/list/get/delete), injected by
    matching intent (coding, debugging, architecture, testing, review, general)."""
    await ensure_initialized()
    _audit_log("manage_skill")
    return await manage_skill_impl(params, skill_store, memory_manager)


@mcp.tool()
async def curate_skills(params: CurateSkillsParams) -> CurateSkillsResponse:
    """Consolidate the distilled-skill library: cluster near-duplicates and return a
    passthrough merge prompt; with apply=True also delete exact dupes (+ never-used
    stale skills via stale_unused_days). Dry by default."""
    await ensure_initialized()
    _audit_log("curate_skills")
    return await curate_skills_impl(params, skill_store, memory_manager)


@mcp.tool()
async def cleanup_memory(params: CleanupMemoryParams) -> CleanupMemoryResponse:
    """Controlled memory cleanup (dry_run=True by default). Modes: older_than_days,
    stale_only, dedup (exact checksum). Preview before deleting."""
    await ensure_initialized()
    _audit_log("cleanup_memory")
    return await cleanup_memory_impl(params, memory_manager)


@mcp.tool()
async def generate_diagram(params: GenerateDiagramParams) -> GenerateDiagramResponse:
    """Mermaid diagram from the indexed code graph.

    Deterministic (no LLM): class, call, module, overview.
    LLM/passthrough (grounded in the subgraph + source): sequence, usecase, state, erd, flow
    (flow = plain-English, non-dev). With no provider, LLM types return passthrough=True +
    passthrough_prompt for the host to draw. Pass scope to narrow (file/class/function/memory_id).
    """
    await ensure_initialized()
    _audit_log("generate_diagram")
    return await generate_diagram_impl(params, memory_manager, llm_adapter)


@mcp.tool()
async def ask(params: AskParams) -> AskResponse:
    """Answer a plain-English question about the codebase, grounded in the indexed
    code with numbered [n] file:line citations. Hybrid-recalls the most relevant
    chunks; with no LLM provider returns passthrough=True + passthrough_prompt for
    the host model to answer in chat (citations are returned either way)."""
    await ensure_initialized()
    _audit_log("ask")
    return await ask_impl(params, memory_manager, llm_adapter)


@mcp.tool()
async def overview(params: OverviewParams) -> OverviewResponse:
    """Architectural system map of the indexed repo: subsystems (by path), most-called
    symbols, dependency edge counts, languages, plus the deterministic overview Mermaid.
    With no LLM provider returns passthrough=True + passthrough_prompt for the host to
    name each subsystem's responsibility; the structural facts are returned either way."""
    await ensure_initialized()
    _audit_log("overview")
    return await overview_impl(params, memory_manager, llm_adapter)


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
