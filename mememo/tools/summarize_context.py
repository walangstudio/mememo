"""
summarize_context tool - Summarize multiple memories.

Creates hierarchical summaries with:
- Grouping by file, type, or none
- Token-limited summaries
- One-line summaries for each memory
"""

import logging
from typing import TYPE_CHECKING

from ..utils.token_counter import count_tokens
from .schemas import SummarizeContextParams, SummarizeContextResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def summarize_context(
    params: SummarizeContextParams, memory_manager: "MemoryManager"
) -> SummarizeContextResponse:
    """
    Summarize multiple memories into a hierarchical summary.

    Args:
        params: Summarize parameters
        memory_manager: Memory manager instance

    Returns:
        Summarize response with summary text
    """
    try:
        # Use memory manager's summarize_memories method
        summary = await memory_manager.summarize_memories(
            memory_ids=params.memory_ids,
            max_tokens=params.max_tokens,
        )

        # Count tokens in summary
        token_count = count_tokens(summary)

        return SummarizeContextResponse(
            success=True,
            summary=summary,
            message=f"Summarized {len(params.memory_ids)} memories",
            token_count=token_count,
            memories_included=len(params.memory_ids),
        )

    except ValueError as e:
        # Memory not found
        logger.warning(f"Failed to summarize: {e}")
        return SummarizeContextResponse(
            success=False,
            summary="",
            message=f"Failed to summarize: {str(e)}",
            token_count=0,
            memories_included=0,
        )

    except Exception as e:
        # Unexpected error
        logger.error(f"Error summarizing context: {e}", exc_info=True)
        return SummarizeContextResponse(
            success=False,
            summary="",
            message=f"Error summarizing context: {str(e)}",
            token_count=0,
            memories_included=0,
        )
