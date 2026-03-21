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

RECALL_TYPES = {"decision", "analysis", "context", "conversation"}


async def recall_context(
    params: RecallContextParams, memory_manager: "MemoryManager"
) -> RecallContextResponse:
    try:
        search_params = SearchParams(
            query=params.query,
            top_k=params.top_k * 3,
            min_similarity=params.min_similarity,
            include_stale=False,
        )
        results = await memory_manager.search_similar(search_params)
        filtered = [r for r in results if r.memory.content.type in RECALL_TYPES]
        filtered = filtered[: params.top_k]
        search_results = [SearchResult(memory=r.memory, similarity=r.similarity) for r in filtered]
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
