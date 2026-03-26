"""
index_repository tool - Batch index a repository.

Indexes repository with:
- Code-aware chunking (Python AST, tree-sitter)
- Incremental indexing (Merkle DAG change detection)
- File pattern matching (glob patterns)
- Progress tracking
- Batch embedding generation
"""

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..chunking import ChunkerFactory
from ..types.memory import CreateMemoryParams, MemoryRelationships
from .schemas import IndexRepositoryParams, IndexRepositoryResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def index_repository(
    params: IndexRepositoryParams,
    memory_manager: "MemoryManager",
    ignored_dirs: list[str] | None = None,
) -> IndexRepositoryResponse:
    """
    Index a repository with code-aware chunking.

    Args:
        params: Index parameters
        memory_manager: Memory manager instance
        ignored_dirs: Directory names to exclude from indexing

    Returns:
        Index response with statistics
    """
    start_time = time.time()

    try:
        # Validate repo path
        repo_path = Path(params.repo_path).resolve()
        if not repo_path.exists():
            return IndexRepositoryResponse(
                success=False,
                message=f"Repository path not found: {params.repo_path}",
                files_indexed=0,
                chunks_created=0,
                files_skipped=0,
                duration_seconds=0,
            )

        if not repo_path.is_dir():
            return IndexRepositoryResponse(
                success=False,
                message=f"Repository path is not a directory: {params.repo_path}",
                files_indexed=0,
                chunks_created=0,
                files_skipped=0,
                duration_seconds=0,
            )

        # Find matching files (excluding ignored directories)
        skip = frozenset(ignored_dirs) if ignored_dirs else None
        files_to_index = _find_matching_files(
            repo_path, params.file_patterns, params.max_files, skip
        )

        logger.info(f"Found {len(files_to_index)} files matching patterns")

        # Incremental indexing with Merkle DAG
        if params.incremental:
            from ..indexing.merkle_dag import MerkleDAG

            merkle = MerkleDAG(memory_manager.storage_manager.base_dir / "merkle")
            files_to_index = merkle.get_changed_files(files_to_index)
            logger.info(f"Incremental: {len(files_to_index)} files changed since last index")

        # Initialize chunker factory
        chunker_factory = ChunkerFactory()

        # Index each file
        files_indexed = 0
        chunks_created = 0
        skip_reasons: dict[str, int] = {}

        def _skip(reason: str) -> None:
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

        for file_path in files_to_index:
            try:
                # Read file content
                content = file_path.read_text(encoding="utf-8")

                # Chunk file with code-aware chunking
                chunks = chunker_factory.chunk_file(content, str(file_path))

                if not chunks:
                    _skip("empty_chunks")
                    logger.debug(f"Skipped {file_path.relative_to(repo_path)} (0 chunks produced)")
                    continue

                # Store each chunk as a memory
                for chunk in chunks:
                    create_params = CreateMemoryParams(
                        content=chunk.text,
                        type="code_snippet",
                        language=chunk.language,
                        file_path=str(file_path.relative_to(repo_path)),
                        line_range=(chunk.start_line, chunk.end_line) if chunk.start_line else None,
                        # Code-aware metadata
                        function_name=chunk.function_name,
                        class_name=chunk.class_name,
                        docstring=chunk.docstring,
                        decorators=chunk.decorators,
                        parent_class=chunk.parent_class,
                        tags=["indexed", "repository"],
                        relationships=MemoryRelationships(),
                    )

                    # Create memory (with embedding, using repo path for git context)
                    await memory_manager.create_memory(create_params, cwd=str(repo_path))
                    chunks_created += 1

                files_indexed += 1
                logger.debug(f"Indexed {file_path.relative_to(repo_path)} ({len(chunks)} chunks)")

            except UnicodeDecodeError:
                _skip("binary")
                logger.debug(f"Skipped binary file: {file_path}")
            except Exception as e:
                _skip("error")
                logger.warning(f"Error indexing {file_path}: {e}")

        duration = time.time() - start_time
        files_skipped = sum(skip_reasons.values())

        # Record the commit hash at time of indexing so sync_commits can diff from here
        try:
            context = await memory_manager.git_manager.detect_context(str(repo_path))
            memory_manager.storage_manager.set_last_indexed_commit(
                context.repo.id, context.branch.name, context.branch.commit_hash
            )
        except Exception as e:
            logger.warning(f"Could not record indexed commit (non-git repo?): {e}")

        msg = f"Indexed {files_indexed} files ({chunks_created} chunks) in {duration:.2f}s"
        if skip_reasons:
            reason_parts = [f"{count} {reason}" for reason, count in sorted(skip_reasons.items())]
            msg += f" | Skipped: {', '.join(reason_parts)}"

        return IndexRepositoryResponse(
            success=True,
            message=msg,
            files_indexed=files_indexed,
            chunks_created=chunks_created,
            files_skipped=files_skipped,
            skip_reasons=skip_reasons,
            duration_seconds=duration,
        )

    except Exception as e:
        # Unexpected error
        duration = time.time() - start_time
        logger.error(f"Error indexing repository: {e}", exc_info=True)
        return IndexRepositoryResponse(
            success=False,
            message=f"Error indexing repository: {str(e)}",
            files_indexed=0,
            chunks_created=0,
            files_skipped=0,
            duration_seconds=duration,
        )


_DEFAULT_IGNORED_DIRS = frozenset(
    {
        "__pycache__",
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "env",
        "node_modules",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "build",
        "dist",
        ".next",
        ".nuxt",
        "target",
        ".idea",
        ".vscode",
        ".coverage",
    }
)


def _find_matching_files(
    repo_path: Path,
    patterns: list[str],
    max_files: int,
    ignored_dirs: frozenset[str] | None = None,
) -> list[Path]:
    """
    Find files matching glob patterns, excluding ignored directories.

    Args:
        repo_path: Repository root path
        patterns: List of glob patterns
        max_files: Maximum files to return
        ignored_dirs: Directory names to exclude (defaults to common ignored dirs)

    Returns:
        List of matching file paths
    """
    skip_dirs = ignored_dirs if ignored_dirs is not None else _DEFAULT_IGNORED_DIRS
    matching_files = set()

    for pattern in patterns:
        for file_path in repo_path.glob(pattern):
            if not file_path.is_file():
                continue

            # Skip files inside ignored directories
            if skip_dirs.intersection(file_path.relative_to(repo_path).parts):
                continue

            matching_files.add(file_path)

            if len(matching_files) >= max_files:
                break

        if len(matching_files) >= max_files:
            break

    return sorted(matching_files)[:max_files]
