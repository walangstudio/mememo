"""Shared construction of the core MemoryManager stack.

Both the MCP server (`initialize_mememo`) and the `mememo index` CLI build the
same Storage + Embedder + VectorIndex + MemoryManager wiring. Keeping it in one
factory stops the two paths from drifting (vector-index base path, secret-scan /
auto-sanitize flags, repo-id fallback must stay identical or CLI-indexed
memories land in a different shard / skip a security check than the server).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .memory_manager import MemoryManager

VECTOR_INDEX_SUBDIR = "vector_index"


async def build_memory_manager(
    config, repo_path: str | None = None
) -> tuple[MemoryManager, str, str]:
    """Construct a MemoryManager from config; return (manager, repo_id, branch).

    repo_path selects the git context (None → cwd). The repo-id fallback for a
    non-git / undetectable context honors MEMEMO_REPO_ID before GLOBAL_REPO_ID,
    matching the server so the CLI and server agree on the shard.
    """
    from ..embeddings.embedder import Embedder
    from .git_manager import GitManager
    from .identity import GLOBAL_REPO_ID
    from .memory_manager import MemoryManager
    from .storage_manager import StorageManager
    from .vector_index import VectorIndex

    base = Path(config.storage.base_dir).expanduser()
    base.mkdir(parents=True, exist_ok=True)

    git_manager = GitManager()
    embedder = Embedder(
        model_name=config.embedding.model_name,
        device=config.embedding.device,
        batch_size=config.embedding.batch_size,
    )

    try:
        ctx = await git_manager.detect_context(repo_path)
        repo_id, branch = ctx.repo.id, ctx.branch.name
    except RuntimeError:
        # Non-git / undetectable context (detect_context's documented signal) —
        # don't swallow programming errors here.
        repo_id = os.environ.get("MEMEMO_REPO_ID", "").strip() or GLOBAL_REPO_ID
        branch = "main"

    vector_index = VectorIndex(
        base_path=base / VECTOR_INDEX_SUBDIR,
        repo_id=repo_id,
        branch=branch,
        dimension=embedder.dimension,
    )
    memory_manager = MemoryManager(
        git_manager=git_manager,
        storage_manager=StorageManager(base_dir=base),
        embedder=embedder,
        vector_index=vector_index,
        auto_sanitize=config.security.auto_sanitize,
        secrets_detection=config.security.secrets_detection,
    )
    return memory_manager, repo_id, branch
