"""
retrieve_memory tool - Retrieve a memory by ID.

Retrieves memory with automatic git context filtering (branch isolation).
"""

import logging
from typing import TYPE_CHECKING

from .schemas import RetrieveMemoryParams, RetrieveMemoryResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def retrieve_memory(
    params: RetrieveMemoryParams, memory_manager: "MemoryManager"
) -> RetrieveMemoryResponse:
    """
    Retrieve a memory by ID.

    Args:
        params: Retrieve memory parameters
        memory_manager: Memory manager instance

    Returns:
        Retrieve memory response with memory object
    """
    try:
        # Retrieve memory via memory manager
        memory = await memory_manager.retrieve_memory(params.memory_id, cwd=params.repo_path)

        return RetrieveMemoryResponse(
            success=True,
            memory=memory,
            message=f"Memory retrieved successfully (ID: {params.memory_id})",
        )

    except ValueError as e:
        # Memory not found
        logger.warning(f"Memory not found: {params.memory_id}")
        return RetrieveMemoryResponse(
            success=False,
            memory=None,
            message=f"Memory not found: {str(e)}",
        )

    except Exception as e:
        # Unexpected error
        logger.error(f"Error retrieving memory: {e}", exc_info=True)
        return RetrieveMemoryResponse(
            success=False,
            memory=None,
            message=f"Error retrieving memory: {str(e)}",
        )
