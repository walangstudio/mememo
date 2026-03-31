"""
refresh_memory tool - Update an existing memory.

Updates memory with:
- New content (re-generates embedding if changed)
- New tags
- Automatic timestamp updates
- Git context preservation
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from .schemas import RefreshMemoryParams, RefreshMemoryResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def refresh_memory(
    params: RefreshMemoryParams, memory_manager: "MemoryManager"
) -> RefreshMemoryResponse:
    """
    Refresh (update) an existing memory.

    Args:
        params: Refresh parameters
        memory_manager: Memory manager instance

    Returns:
        Refresh response with updated memory
    """
    try:
        # Retrieve existing memory
        memory = await memory_manager.retrieve_memory(params.memory_id, cwd=params.repo_path)

        # Update content if provided
        if params.new_content is not None:
            # Delete old memory
            await memory_manager.delete_memory(params.memory_id, cwd=params.repo_path)

            # Create new memory with updated content
            from ..types.memory import CreateMemoryParams, MemoryRelationships

            create_params = CreateMemoryParams(
                content=params.new_content,
                type=memory.content.type,
                language=memory.content.language,
                file_path=memory.content.file_path,
                line_range=memory.content.line_range,
                tags=params.tags if params.tags is not None else memory.metadata.tags,
                # Preserve code-aware metadata
                function_name=memory.content.function_name,
                class_name=memory.content.class_name,
                docstring=memory.content.docstring,
                decorators=memory.content.decorators,
                parent_class=memory.content.parent_class,
                relationships=memory.relationships or MemoryRelationships(),
            )

            # Create updated memory
            updated_memory = await memory_manager.create_memory(create_params, cwd=params.repo_path)

            return RefreshMemoryResponse(
                success=True,
                message=f"Memory refreshed successfully (new ID: {updated_memory.id})",
                memory=updated_memory,
            )

        # Update tags only
        elif params.tags is not None:
            # Update tags in place
            memory.metadata.tags = params.tags
            memory.metadata.updated_at = datetime.now()

            # Save updated memory
            await memory_manager.storage_manager.save_memory(memory)

            return RefreshMemoryResponse(
                success=True,
                message=f"Memory tags updated successfully (ID: {memory.id})",
                memory=memory,
            )

        else:
            # No updates provided
            return RefreshMemoryResponse(
                success=False,
                message="No updates provided (specify new_content or tags)",
                memory=None,
            )

    except ValueError as e:
        # Memory not found
        logger.warning(f"Memory not found: {params.memory_id}")
        return RefreshMemoryResponse(
            success=False,
            message=f"Memory not found: {str(e)}",
            memory=None,
        )

    except Exception as e:
        # Unexpected error
        logger.error(f"Error refreshing memory: {e}", exc_info=True)
        return RefreshMemoryResponse(
            success=False,
            message=f"Error refreshing memory: {str(e)}",
            memory=None,
        )
