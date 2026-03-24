"""
store_decision tool - Store structured architectural decisions.

Assembles canonical markdown from structured fields before storing as a
decision memory type.
"""

import logging
from typing import TYPE_CHECKING

from ..types.memory import CreateMemoryParams, MemoryRelationships
from .schemas import StoreDecisionParams, StoreDecisionResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


def _build_decision_content(params: StoreDecisionParams) -> str:
    alternatives_md = "\n".join(f"- {alt}" for alt in params.alternatives)
    outcome = params.outcome or "TBD"
    return (
        f"## Decision\n\n"
        f"**Problem:** {params.problem}\n\n"
        f"**Alternatives:**\n{alternatives_md}\n\n"
        f"**Chosen:** {params.chosen}\n\n"
        f"**Rationale:** {params.rationale}\n\n"
        f"**Outcome:** {outcome}"
    )


async def store_decision(
    params: StoreDecisionParams, memory_manager: "MemoryManager"
) -> StoreDecisionResponse:
    try:
        content = _build_decision_content(params)
        create_params = CreateMemoryParams(
            content=content,
            type="decision",
            tags=params.tags,
            relationships=MemoryRelationships(),
        )
        memory = await memory_manager.create_memory(create_params)
        return StoreDecisionResponse(
            success=True,
            memory_id=memory.id,
            message=f"Decision stored successfully (ID: {memory.id})",
            token_count=memory.metadata.token_count,
            checksum=memory.metadata.checksum,
        )
    except ValueError as e:
        logger.warning(f"Failed to store decision: {e}")
        return StoreDecisionResponse(
            success=False,
            memory_id="",
            message=f"Failed to store decision: {str(e)}",
            token_count=0,
            checksum="",
        )
    except Exception as e:
        logger.error(f"Error storing decision: {e}", exc_info=True)
        return StoreDecisionResponse(
            success=False,
            memory_id="",
            message=f"Error storing decision: {str(e)}",
            token_count=0,
            checksum="",
        )
