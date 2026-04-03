"""
delete_memory tool - Delete a memory by ID.

Deletes memory with:
- Confirmation check (must be True)
- Storage deletion (SQLite + JSON blob)
- Vector index cleanup
- Git context filtering (branch isolation)
"""

import logging
from typing import TYPE_CHECKING

from .schemas import DeleteMemoryParams, DeleteMemoryResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def delete_memory(
    params: DeleteMemoryParams, memory_manager: "MemoryManager"
) -> DeleteMemoryResponse:
    """
    Delete a memory by ID.

    Args:
        params: Delete parameters with confirmation
        memory_manager: Memory manager instance

    Returns:
        Delete response with success status
    """
    try:
        # Check confirmation
        if not params.confirm:
            return DeleteMemoryResponse(
                success=False,
                message="Deletion not confirmed (set confirm=True)",
                memory_id=params.memory_id,
            )

        # Delete memory via memory manager
        await memory_manager.delete_memory(params.memory_id, cwd=params.repo_path)

        return DeleteMemoryResponse(
            success=True,
            message=f"Memory deleted successfully (ID: {params.memory_id})",
            memory_id=params.memory_id,
        )

    except ValueError as e:
        # Memory not found
        logger.warning(f"Memory not found: {params.memory_id}")
        return DeleteMemoryResponse(
            success=False,
            message=f"Memory not found: {str(e)}",
            memory_id=params.memory_id,
        )

    except Exception as e:
        # Unexpected error
        logger.error(f"Error deleting memory: {e}", exc_info=True)
        return DeleteMemoryResponse(
            success=False,
            message=f"Error deleting memory: {str(e)}",
            memory_id=params.memory_id,
        )
