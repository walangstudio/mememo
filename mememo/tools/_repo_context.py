"""Shared (repo_id, branch) resolution for tools that scope to a single lane.

Detecting git context is identical across the diagram / comprehension tools, so
they all route through here to avoid the contract drifting between copies.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def resolve_repo_branch(
    repo_id: str | None,
    branch: str | None,
    repo_path: str | None,
    memory_manager: MemoryManager,
) -> tuple[str, str]:
    """Resolve (repo_id, branch), detecting from git only when not both given.

    Returns empty strings for whatever couldn't be resolved (git detection failed
    and no override was passed); callers decide whether an empty lane is usable.
    """
    if repo_id and branch:
        return repo_id, branch
    try:
        ctx = await memory_manager.git_manager.detect_context(repo_path or ".")
        return repo_id or ctx.repo.id, branch or ctx.branch.name
    except Exception as exc:
        logger.warning("resolve_repo_branch: git context detection failed: %s", exc)
        return repo_id or "", branch or ""
