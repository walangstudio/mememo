"""
summarize_context tool - Summarize multiple memories or raw text.

Creates hierarchical summaries with:
- Grouping by file, type, or none
- Token-limited summaries
- One-line summaries for each memory
- Direct text summarization (truncation)
"""

import logging
from typing import TYPE_CHECKING

from ..types.memory import CreateMemoryParams, MemoryRelationships
from ..utils.token_counter import count_tokens, truncate_to_tokens
from .schemas import SummarizeContextParams, SummarizeContextResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def summarize_context(
    params: SummarizeContextParams, memory_manager: "MemoryManager"
) -> SummarizeContextResponse:
    try:
        if not params.text and not params.memory_ids:
            return SummarizeContextResponse(
                success=False,
                summary="",
                message="Either text or memory_ids must be provided",
                token_count=0,
                memories_included=0,
            )

        if params.text:
            summary = truncate_to_tokens(params.text, params.max_tokens)
            memories_included = 0
        else:
            summary = await memory_manager.summarize_memories(
                memory_ids=params.memory_ids,
                max_tokens=params.max_tokens,
                cwd=params.repo_path,
            )
            memories_included = len(params.memory_ids)

        token_count = count_tokens(summary)

        saved_memory_id = None
        if params.save_as_memory:
            create_params = CreateMemoryParams(
                content=summary,
                type="summary",
                relationships=MemoryRelationships(),
            )
            saved_memory = await memory_manager.create_memory(create_params, cwd=params.repo_path)
            saved_memory_id = saved_memory.id

        return SummarizeContextResponse(
            success=True,
            summary=summary,
            message=f"Summarized {'text' if params.text else f'{memories_included} memories'}",
            token_count=token_count,
            memories_included=memories_included,
            saved_memory_id=saved_memory_id,
        )

    except ValueError as e:
        logger.warning(f"Failed to summarize: {e}")
        return SummarizeContextResponse(
            success=False,
            summary="",
            message=f"Failed to summarize: {str(e)}",
            token_count=0,
            memories_included=0,
        )

    except Exception as e:
        logger.error(f"Error summarizing context: {e}", exc_info=True)
        return SummarizeContextResponse(
            success=False,
            summary="",
            message=f"Error summarizing context: {str(e)}",
            token_count=0,
            memories_included=0,
        )
