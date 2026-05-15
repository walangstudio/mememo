"""
search_similar tool - Semantic similarity search.

Searches for similar memories using vector embeddings with:
- Automatic embedding generation
- FAISS vector similarity search
- Git context filtering (branch isolation)
- Type and language filters
"""

import logging
from typing import TYPE_CHECKING

from ..types.memory import SearchParams
from .schemas import SearchResult, SearchSimilarParams, SearchSimilarResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def search_similar(
    params: SearchSimilarParams, memory_manager: "MemoryManager"
) -> SearchSimilarResponse:
    """
    Search for similar memories using semantic search.

    Args:
        params: Search parameters
        memory_manager: Memory manager instance

    Returns:
        Search response with ranked results
    """
    try:
        # Convert to SearchParams
        search_params = SearchParams(
            query=params.query,
            top_k=params.top_k,
            min_similarity=params.min_similarity,
            type=params.type,
            include_stale=params.include_stale,
            tags=params.tags,
        )

        # Search via memory manager
        results = await memory_manager.search_similar(search_params, cwd=params.repo_path)

        # Apply language filter if specified
        if params.language:
            results = [r for r in results if r.memory.content.language == params.language]

        # v0.5 (FR-023): cluster filter — restrict to memories whose relations
        # land in the given community. Requires a prior clustering pass.
        if params.cluster_id is not None:
            conn = memory_manager.storage_manager.conn
            rows = conn.execute(
                "SELECT DISTINCT source_memory_id AS mid FROM relations "
                "WHERE community = ? "
                "UNION "
                "SELECT DISTINCT target_memory_id AS mid FROM relations "
                "WHERE community = ? AND target_memory_id IS NOT NULL",
                (params.cluster_id, params.cluster_id),
            ).fetchall()
            allowed = {row["mid"] for row in rows}
            results = [r for r in results if r.memory.id in allowed]

        # Convert to search result schema
        search_results = [SearchResult(memory=r.memory, similarity=r.similarity) for r in results]

        return SearchSimilarResponse(
            success=True,
            results=search_results,
            message=f"Found {len(search_results)} similar memories",
            count=len(search_results),
        )

    except Exception as e:
        # Unexpected error
        logger.error(f"Error searching memories: {e}", exc_info=True)
        return SearchSimilarResponse(
            success=False,
            results=[],
            message=f"Error searching memories: {str(e)}",
            count=0,
        )
