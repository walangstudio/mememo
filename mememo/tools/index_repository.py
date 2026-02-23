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
from typing import TYPE_CHECKING, List

from ..chunking import ChunkerFactory
from ..types.memory import CreateMemoryParams, MemoryRelationships
from .schemas import IndexRepositoryParams, IndexRepositoryResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def index_repository(
    params: IndexRepositoryParams, memory_manager: "MemoryManager"
) -> IndexRepositoryResponse:
    """
    Index a repository with code-aware chunking.

    Args:
        params: Index parameters
        memory_manager: Memory manager instance

    Returns:
        Index response with statistics
    """
    start_time = time.time()

    try:
        # Validate repo path
        repo_path = Path(params.repo_path)
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

        # Find matching files
        files_to_index = _find_matching_files(
            repo_path, params.file_patterns, params.max_files
        )

        logger.info(f"Found {len(files_to_index)} files matching patterns")

        # Incremental indexing with Merkle DAG
        if params.incremental:
            from ..indexing.merkle_dag import MerkleDAG

            merkle = MerkleDAG(
                memory_manager.storage_manager.base_dir / "merkle"
            )
            files_to_index = merkle.get_changed_files(files_to_index)
            logger.info(
                f"Incremental: {len(files_to_index)} files changed since last index"
            )

        # Initialize chunker factory
        chunker_factory = ChunkerFactory()

        # Index each file
        files_indexed = 0
        chunks_created = 0
        files_skipped = 0

        for file_path in files_to_index:
            try:
                # Read file content
                content = file_path.read_text(encoding="utf-8")

                # Chunk file with code-aware chunking
                chunks = chunker_factory.chunk_file(content, str(file_path))

                # Store each chunk as a memory
                for chunk in chunks:
                    create_params = CreateMemoryParams(
                        content=chunk.text,
                        type="code_snippet",
                        language=chunk.language,
                        file_path=str(file_path.relative_to(repo_path)),
                        line_range=(chunk.start_line, chunk.end_line)
                        if chunk.start_line
                        else None,
                        # Code-aware metadata
                        function_name=chunk.function_name,
                        class_name=chunk.class_name,
                        docstring=chunk.docstring,
                        decorators=chunk.decorators,
                        parent_class=chunk.parent_class,
                        tags=["indexed", "repository"],
                        relationships=MemoryRelationships(),
                    )

                    # Create memory (with embedding)
                    await memory_manager.create_memory(create_params)
                    chunks_created += 1

                files_indexed += 1
                logger.debug(
                    f"Indexed {file_path.relative_to(repo_path)} ({len(chunks)} chunks)"
                )

            except UnicodeDecodeError:
                # Binary file - skip
                files_skipped += 1
                logger.debug(f"Skipped binary file: {file_path}")
            except Exception as e:
                # File processing error - skip and continue
                files_skipped += 1
                logger.warning(f"Error indexing {file_path}: {e}")

        duration = time.time() - start_time

        return IndexRepositoryResponse(
            success=True,
            message=f"Indexed {files_indexed} files ({chunks_created} chunks) in {duration:.2f}s",
            files_indexed=files_indexed,
            chunks_created=chunks_created,
            files_skipped=files_skipped,
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


def _find_matching_files(
    repo_path: Path, patterns: List[str], max_files: int
) -> List[Path]:
    """
    Find files matching glob patterns.

    Args:
        repo_path: Repository root path
        patterns: List of glob patterns
        max_files: Maximum files to return

    Returns:
        List of matching file paths
    """
    matching_files = set()

    for pattern in patterns:
        # Use glob to find matching files
        for file_path in repo_path.glob(pattern):
            if file_path.is_file():
                matching_files.add(file_path)

                # Check max files limit
                if len(matching_files) >= max_files:
                    break

        if len(matching_files) >= max_files:
            break

    return sorted(matching_files)[:max_files]
