"""
check_memory tool - Get memory statistics.

Returns statistics about:
- Total memories and storage size
- Vector index stats (vectors, shards)
- Embedder info (model, dimension, device)
- Git context (optional)
"""

import logging
from typing import TYPE_CHECKING

from .schemas import CheckMemoryParams, CheckMemoryResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def check_memory(
    params: CheckMemoryParams, memory_manager: "MemoryManager"
) -> CheckMemoryResponse:
    """
    Get memory statistics and info.

    Args:
        params: Check parameters
        memory_manager: Memory manager instance

    Returns:
        Check response with statistics
    """
    try:
        # Get statistics from memory manager
        statistics = memory_manager.get_statistics()

        # Get git context if requested
        git_context = None
        if params.include_git_info:
            try:
                context = await memory_manager.git_manager.detect_context(cwd=params.repo_path)
                git_context = {
                    "repo": context.repo.model_dump() if context.repo else None,
                    "branch": context.branch.model_dump() if context.branch else None,
                }
            except Exception as e:
                logger.warning(f"Failed to get git context: {e}")

        return CheckMemoryResponse(
            success=True,
            message="Memory statistics retrieved successfully",
            statistics=statistics,
            git_context=git_context,
        )

    except Exception as e:
        # Unexpected error
        logger.error(f"Error checking memory: {e}", exc_info=True)
        return CheckMemoryResponse(
            success=False,
            message=f"Error checking memory: {str(e)}",
            statistics={},
            git_context=None,
        )
