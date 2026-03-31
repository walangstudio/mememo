"""
recent_context tool - Return N most recent memories, optionally filtered by type.

Pure SQL path — no vector search.
"""

import logging
from typing import TYPE_CHECKING

from ..types.memory import MemoryFilters
from .schemas import RecentContextParams, RecentContextResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def recent_context(
    params: RecentContextParams, memory_manager: "MemoryManager"
) -> RecentContextResponse:
    try:
        filters = MemoryFilters(
            type=params.type,
            sort_by="date",
            limit=params.limit,
            include_stale=False,
        )
        memories = await memory_manager.find_memories(filters, cwd=params.repo_path)
        memories = memories[: params.limit]
        return RecentContextResponse(
            success=True,
            memories=memories,
            message=f"Found {len(memories)} recent memories",
            count=len(memories),
        )
    except Exception as e:
        logger.error(f"Error fetching recent context: {e}", exc_info=True)
        return RecentContextResponse(
            success=False,
            memories=[],
            message=f"Error fetching recent context: {str(e)}",
            count=0,
        )
