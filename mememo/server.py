"""
mememo v0.1.0 - FastMCP Server

All-Python code-aware memory server with:
- Multi-language code parsing (Python, TypeScript, Go, Rust, Java, C/C++, C#)
- Git-aware branch isolation
- Semantic vector search (FAISS)
- Security-first (secrets detection)
- Hybrid storage (SQLite + JSON blobs)
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP

from .types.config import MemoConfig
from .core.git_manager import GitManager
from .core.storage_manager import StorageManager
from .core.vector_index import VectorIndex
from .core.memory_manager import MemoryManager
from .embeddings.embedder import Embedder
from .tools.schemas import (
    StoreMemoryParams,
    StoreMemoryResponse,
    RetrieveMemoryParams,
    RetrieveMemoryResponse,
    SearchSimilarParams,
    SearchSimilarResponse,
    ListMemoriesParams,
    ListMemoriesResponse,
    SummarizeContextParams,
    SummarizeContextResponse,
    DeleteMemoryParams,
    DeleteMemoryResponse,
    IndexRepositoryParams,
    IndexRepositoryResponse,
    CheckMemoryParams,
    CheckMemoryResponse,
    RefreshMemoryParams,
    RefreshMemoryResponse,
)
from .tools import (
    store_memory as store_memory_impl,
    retrieve_memory as retrieve_memory_impl,
    search_similar as search_similar_impl,
    list_memories as list_memories_impl,
    summarize_context as summarize_context_impl,
    delete_memory as delete_memory_impl,
    index_repository as index_repository_impl,
    check_memory as check_memory_impl,
    refresh_memory as refresh_memory_impl,
)

# Initialize logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Initialize FastMCP server
mcp = FastMCP("mememo", version="0.1.0")

# Global state (initialized on startup)
config: Optional[MemoConfig] = None
memory_manager: Optional[MemoryManager] = None


@mcp.resource("config://mememo")
async def get_config() -> str:
    """
    Get current mememo configuration.

    Returns configuration as formatted text.
    """
    await ensure_initialized()

    return f"""mememo v0.1.0 Configuration:

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
  Default top-k: {config.search.default_top_k}
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

    return f"""mememo v0.1.0 Statistics:

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
    """
    global config, memory_manager

    logger.info("=" * 60)
    logger.info("Initializing mememo v0.1.0")
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

    logger.info("mememo v0.1.0 initialized successfully!")


async def ensure_initialized():
    """Ensure mememo is initialized (lazy initialization)."""
    global config, memory_manager
    if memory_manager is None:
        await initialize_mememo()


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
    return await store_memory_impl(params, memory_manager)


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
    return await search_similar_impl(params, memory_manager)


@mcp.tool()
async def list_memories(params: ListMemoriesParams) -> ListMemoriesResponse:
    """
    List memories with filters.

    Filters:
    - Type (code_snippet, context, summary, relationship)
    - Language (python, typescript, javascript, go, rust, etc.)
    - Tags (user-defined tags)
    - File path, function name, class name
    - Git context (automatic branch isolation)

    Returns matching memories (up to limit).
    """
    await ensure_initialized()
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
    return await index_repository_impl(params, memory_manager)


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
    return await check_memory_impl(params, memory_manager)


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
    return await refresh_memory_impl(params, memory_manager)


def run():
    """Run the FastMCP server."""
    mcp.run()


if __name__ == "__main__":
    run()
