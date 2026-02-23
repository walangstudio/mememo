"""
list_memories tool - List memories with filters.

Lists memories with support for:
- Type, language, tags filters
- File path, function name, class name filters
- Git context filtering (branch isolation)
- Limit and pagination
"""

import logging
from typing import TYPE_CHECKING

from ..types.memory import MemoryFilters
from .schemas import ListMemoriesParams, ListMemoriesResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def list_memories(
    params: ListMemoriesParams, memory_manager: "MemoryManager"
) -> ListMemoriesResponse:
    """
    List memories with filters.

    Args:
        params: List parameters with filters
        memory_manager: Memory manager instance

    Returns:
        List response with matching memories
    """
    try:
        # Convert to MemoryFilters
        filters = MemoryFilters(
            type=params.type,
            language=params.language,
            tags=params.tags,
            file_path=params.file_path,
            function_name=params.function_name,
            class_name=params.class_name,
        )

        # Find memories via memory manager
        memories = await memory_manager.find_memories(filters)

        # Get total count before limiting
        total = len(memories)

        # Apply limit
        memories = memories[: params.limit]

        return ListMemoriesResponse(
            success=True,
            memories=memories,
            message=f"Found {len(memories)} memories (total: {total})",
            count=len(memories),
            total=total,
        )

    except Exception as e:
        # Unexpected error
        logger.error(f"Error listing memories: {e}", exc_info=True)
        return ListMemoriesResponse(
            success=False,
            memories=[],
            message=f"Error listing memories: {str(e)}",
            count=0,
            total=0,
        )
