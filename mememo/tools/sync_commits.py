"""
sync_commits tool - Patch memories to reflect new commits.

Diffs HEAD against the last indexed commit, then:
1. Marks code memories stale for every changed file
2. Re-indexes changed files that still exist
3. Records new HEAD as the last indexed commit

Persistent types (decision, analysis, conversation, context, summary)
are never staled — they survive code changes by design.
"""

import fnmatch
import logging
import time
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from ..chunking import ChunkerFactory
from ..indexing.merkle_dag import MerkleDAG
from ..types.memory import CreateMemoryParams, MemoryRelationships
from .schemas import SyncCommitsParams, SyncCommitsResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def sync_commits(
    params: SyncCommitsParams, memory_manager: "MemoryManager"
) -> SyncCommitsResponse:
    """
    Sync memories to new commits by diffing git history.

    Args:
        params: Sync parameters (repo_path, file_patterns)
        memory_manager: Memory manager instance

    Returns:
        Sync response with statistics
    """
    start_time = time.time()

    repo_path = Path(params.repo_path)
    if not repo_path.exists() or not repo_path.is_dir():
        return SyncCommitsResponse(
            success=False,
            message=f"Repository path not found: {params.repo_path}",
        )

    # Detect current git context for this repo
    try:
        context = await memory_manager.git_manager.detect_context(str(repo_path))
    except RuntimeError as e:
        return SyncCommitsResponse(success=False, message=str(e))

    repo_id = context.repo.id
    branch = context.branch.name
    current_commit = context.branch.commit_hash

    # Get the commit we last indexed
    last_commit = memory_manager.storage_manager.get_last_indexed_commit(repo_id, branch)
    if not last_commit:
        return SyncCommitsResponse(
            success=False,
            message=(
                f"No previous index found for {context.repo.name}/{branch}. "
                "Run index_repository first."
            ),
        )

    if last_commit == current_commit:
        return SyncCommitsResponse(
            success=True,
            message=f"Already up to date at {current_commit[:8]}",
            from_commit=current_commit[:8],
            to_commit=current_commit[:8],
        )

    # Get files changed between last indexed commit and HEAD
    try:
        changed_files = await memory_manager.git_manager.get_changed_files(
            last_commit, current_commit, cwd=str(repo_path)
        )
    except RuntimeError as e:
        return SyncCommitsResponse(
            success=False,
            message=f"Failed to get changed files: {e}",
            from_commit=last_commit[:8],
            to_commit=current_commit[:8],
        )

    if not changed_files:
        memory_manager.storage_manager.set_last_indexed_commit(repo_id, branch, current_commit)
        return SyncCommitsResponse(
            success=True,
            message=f"No file changes between {last_commit[:8]}..{current_commit[:8]}",
            from_commit=last_commit[:8],
            to_commit=current_commit[:8],
        )

    # 1. Mark code memories stale for every changed file
    memories_staled = 0
    stale_reason = f"File changed in commit {current_commit[:8]}"
    for file_path in changed_files:
        count = memory_manager.storage_manager.mark_memories_stale_for_file(
            file_path, repo_id, branch, stale_reason
        )
        memories_staled += count
        logger.debug(f"Staled {count} memories for {file_path}")

    # 2. Re-index changed files that still exist and match file_patterns
    existing_files = [
        repo_path / f
        for f in changed_files
        if (repo_path / f).exists() and _matches_patterns(f, params.file_patterns)
    ]
    files_removed = len(changed_files) - len(existing_files)

    chunker_factory = ChunkerFactory()
    merkle = MerkleDAG(memory_manager.storage_manager.base_dir / "merkle")
    chunks_created = 0

    for file_path in existing_files:
        try:
            content = file_path.read_text(encoding="utf-8")
            chunks = chunker_factory.chunk_file(content, str(file_path))

            for chunk in chunks:
                create_params = CreateMemoryParams(
                    content=chunk.text,
                    type="code_snippet",
                    language=chunk.language,
                    file_path=str(file_path.relative_to(repo_path)),
                    line_range=(chunk.start_line, chunk.end_line) if chunk.start_line else None,
                    function_name=chunk.function_name,
                    class_name=chunk.class_name,
                    docstring=chunk.docstring,
                    decorators=chunk.decorators,
                    parent_class=chunk.parent_class,
                    tags=["indexed", "repository", "synced"],
                    relationships=MemoryRelationships(),
                )
                await memory_manager.create_memory(create_params)
                chunks_created += 1

            merkle.mark_file_indexed(file_path)
            logger.debug(f"Re-indexed {file_path.relative_to(repo_path)} ({len(chunks)} chunks)")

        except UnicodeDecodeError:
            logger.debug(f"Skipped binary file: {file_path}")
        except Exception as e:
            logger.warning(f"Error re-indexing {file_path}: {e}")

    # 3. Record new HEAD as the last indexed commit
    memory_manager.storage_manager.set_last_indexed_commit(repo_id, branch, current_commit)

    duration = time.time() - start_time
    return SyncCommitsResponse(
        success=True,
        message=(
            f"Synced {last_commit[:8]}..{current_commit[:8]}: "
            f"{len(changed_files)} files changed, {chunks_created} new chunks, "
            f"{memories_staled} memories staled"
        ),
        from_commit=last_commit[:8],
        to_commit=current_commit[:8],
        files_updated=len(existing_files),
        files_removed=files_removed,
        memories_staled=memories_staled,
        chunks_created=chunks_created,
        duration_seconds=duration,
    )


def _matches_patterns(file_path: str, patterns: list[str]) -> bool:
    """Return True if file_path matches any of the glob patterns."""
    p = PurePosixPath(file_path)
    file_name = p.name
    for pattern in patterns:
        # Extract the filename glob from patterns like "**/*.py" → "*.py"
        name_pattern = pattern.split("/")[-1]
        if fnmatch.fnmatch(file_name, name_pattern):
            return True
    return False
