"""
end_session tool - Store a session summary as a conversation memory.

Prepends ISO timestamp and git branch name to the summary before storing.
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from ..types.memory import CreateMemoryParams, MemoryRelationships
from .schemas import EndSessionParams, EndSessionResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def end_session(
    params: EndSessionParams, memory_manager: "MemoryManager"
) -> EndSessionResponse:
    try:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        try:
            git_context = await memory_manager.git_manager.detect_context(cwd=params.repo_path)
            branch = git_context.branch.name
        except Exception:
            branch = "unknown"

        content = f"## Session — {timestamp} [{branch}]\n\n{params.summary}"
        create_params = CreateMemoryParams(
            content=content,
            type="conversation",
            tags=params.tags,
            relationships=MemoryRelationships(),
        )
        memory = await memory_manager.create_memory(create_params, cwd=params.repo_path)
        return EndSessionResponse(
            success=True,
            memory_id=memory.id,
            message=f"Session summary stored (ID: {memory.id})",
            token_count=memory.metadata.token_count,
            checksum=memory.metadata.checksum,
        )
    except ValueError as e:
        logger.warning(f"Failed to store session summary: {e}")
        return EndSessionResponse(
            success=False,
            memory_id="",
            message=f"Failed to store session summary: {str(e)}",
            token_count=0,
            checksum="",
        )
    except Exception as e:
        logger.error(f"Error storing session summary: {e}", exc_info=True)
        return EndSessionResponse(
            success=False,
            memory_id="",
            message=f"Error storing session summary: {str(e)}",
            token_count=0,
            checksum="",
        )
