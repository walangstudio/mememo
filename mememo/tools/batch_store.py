"""batch_store tool - Store multiple memories in a single batch operation."""

import logging
from typing import TYPE_CHECKING

from ..types.memory import CreateMemoryParams, MemoryRelationships
from .schemas import BatchStoreItemResult, BatchStoreParams, BatchStoreResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def batch_store(
    params: BatchStoreParams, memory_manager: "MemoryManager"
) -> BatchStoreResponse:
    try:
        create_params_list: list[CreateMemoryParams] = []
        for item in params.memories:
            create_params_list.append(
                CreateMemoryParams(
                    content=item.content,
                    type=item.type,
                    language=item.language,
                    file_path=item.file_path,
                    line_range=item.line_range,
                    tags=item.tags,
                    function_name=item.function_name,
                    class_name=item.class_name,
                    docstring=item.docstring,
                    decorators=item.decorators,
                    parent_class=item.parent_class,
                    relationships=MemoryRelationships(),
                )
            )

        cwd = params.repo_path
        memories = await memory_manager.create_memories_batch(create_params_list, cwd=cwd)

        results = [
            BatchStoreItemResult(
                memory_id=m.id,
                success=True,
                message=f"Stored ({m.metadata.token_count} tokens)",
            )
            for m in memories
        ]

        return BatchStoreResponse(
            success=True,
            results=results,
            stored_count=len(memories),
            failed_count=len(params.memories) - len(memories),
        )

    except Exception as e:
        logger.error(f"Batch store failed: {e}", exc_info=True)
        return BatchStoreResponse(
            success=False,
            results=[],
            stored_count=0,
            failed_count=len(params.memories),
        )
