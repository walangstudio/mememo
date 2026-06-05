"""
recall_context tool - Multi-type semantic search across persistent memory types.

Searches decision, analysis, context, and conversation memories only.
Code snippets and relationships are excluded.
"""

import logging
from typing import TYPE_CHECKING

from ..types.memory import SearchParams
from .schemas import RecallContextParams, RecallContextResponse, SearchResult

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

RECALL_TYPES = {"decision", "analysis", "context", "conversation", "reference"}


async def recall_context(
    params: RecallContextParams, memory_manager: "MemoryManager"
) -> RecallContextResponse:
    try:
        # Fanout reduced from 3x to ~1.5x now that type filtering is pushed
        # into load_memories via content_types — no wasted JSON reads for rows
        # whose content_type is outside RECALL_TYPES.
        search_params = SearchParams(
            query=params.query,
            top_k=max(params.top_k + 2, int(params.top_k * 1.5)),
            min_similarity=params.min_similarity,
            include_stale=False,
            tags=params.tags,
            hybrid=True,
        )
        results = await memory_manager.search_similar(
            search_params, cwd=params.repo_path, content_types=RECALL_TYPES
        )
        results = results[: params.top_k]
        search_results = [SearchResult(memory=r.memory, similarity=r.similarity) for r in results]
        return RecallContextResponse(
            success=True,
            results=search_results,
            message=f"Found {len(search_results)} context memories",
            count=len(search_results),
        )
    except Exception as e:
        logger.error(f"Error recalling context: {e}", exc_info=True)
        return RecallContextResponse(
            success=False,
            results=[],
            message=f"Error recalling context: {str(e)}",
            count=0,
        )
