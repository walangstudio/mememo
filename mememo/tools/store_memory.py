"""
store_memory tool - Store code snippets, context, or summaries.

Stores content with automatic:
- Git context detection (repo + branch)
- Secrets detection and sanitization
- Embedding generation
- Vector indexing
- Code-aware metadata extraction
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..types.memory import CreateMemoryParams, MemoryRelationships
from .schemas import StoreMemoryParams, StoreMemoryResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def store_memory(
    params: StoreMemoryParams, memory_manager: "MemoryManager"
) -> StoreMemoryResponse:
    """
    Store a new memory.

    Args:
        params: Store memory parameters
        memory_manager: Memory manager instance

    Returns:
        Store memory response with memory ID
    """
    try:
        # Convert to CreateMemoryParams
        create_params = CreateMemoryParams(
            content=params.content,
            type=params.type,
            language=params.language,
            file_path=params.file_path,
            line_range=params.line_range,
            tags=params.tags,
            # Code-aware metadata
            function_name=params.function_name,
            class_name=params.class_name,
            docstring=params.docstring,
            decorators=params.decorators,
            parent_class=params.parent_class,
            # Empty relationships for now
            relationships=MemoryRelationships(),
        )

        # Derive cwd from file_path if it's absolute
        cwd = None
        if params.file_path and Path(params.file_path).is_absolute():
            cwd = str(Path(params.file_path).parent)

        # Create memory via memory manager
        memory = await memory_manager.create_memory(create_params, cwd=cwd)

        return StoreMemoryResponse(
            success=True,
            memory_id=memory.id,
            message=f"Memory stored successfully (ID: {memory.id})",
            token_count=memory.metadata.token_count,
            checksum=memory.metadata.checksum,
        )

    except ValueError as e:
        # Validation error (e.g., secrets detected)
        logger.warning(f"Failed to store memory: {e}")
        return StoreMemoryResponse(
            success=False,
            memory_id="",
            message=f"Failed to store memory: {str(e)}",
            token_count=0,
            checksum="",
        )

    except Exception as e:
        # Unexpected error
        logger.error(f"Error storing memory: {e}", exc_info=True)
        return StoreMemoryResponse(
            success=False,
            memory_id="",
            message=f"Error storing memory: {str(e)}",
            token_count=0,
            checksum="",
        )
