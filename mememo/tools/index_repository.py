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
from ..chunking.ts_edges import EDGE_WALKERS
from ..types.memory import CreateMemoryParams, MemoryRelationships
from .schemas import IndexRepositoryParams, IndexRepositoryResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# Languages that emit edges in the v0.5 edge pass: Python (AST chunker) plus
# every tree-sitter walker registered in ts_edges.EDGE_WALKERS. Driven by the
# registry so a new language walker is picked up here automatically instead of
# silently producing no edges (regression guard — rust/java/c/cpp/csharp were
# walked but never indexed because this list was hardcoded to the original 5).
EDGE_PASS_LANGUAGES: frozenset[str] = frozenset({"python", "markdown", *EDGE_WALKERS})


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

        # Accumulate (rel_path, content, lang) and SymbolEntry rows during the
        # main loop so the v0.5 edge pass doesn't have to re-walk + re-read +
        # re-query SQLite. Each entry already lives in memory here.
        from ..chunking.python_ast_chunker import file_path_to_module
        from ..core.symbol_resolver import SymbolEntry

        edge_inputs: list[tuple[str, str, str]] = []
        symbols: list[SymbolEntry] = []

        def _skip(reason: str) -> None:
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

        for file_path in files_to_index:
            try:
                content = file_path.read_text(encoding="utf-8")
                chunks = chunker_factory.chunk_file(content, str(file_path))
                if not chunks:
                    _skip("empty_chunks")
                    continue

                rel_path = str(file_path.relative_to(repo_path)).replace("\\", "/")
                module = file_path_to_module(rel_path)
                file_lang = chunks[0].language

                for chunk in chunks:
                    create_params = CreateMemoryParams(
                        content=chunk.text,
                        type="code_snippet",
                        language=chunk.language,
                        file_path=rel_path,
                        line_range=(chunk.start_line, chunk.end_line) if chunk.start_line else None,
                        function_name=chunk.function_name,
                        class_name=chunk.class_name,
                        docstring=chunk.docstring,
                        decorators=chunk.decorators,
                        parent_class=chunk.parent_class,
                        tags=["indexed", "repository"],
                        relationships=MemoryRelationships(),
                    )
                    memory = await memory_manager.create_memory(create_params, cwd=str(repo_path))
                    chunks_created += 1

                    parts = [module]
                    if chunk.class_name:
                        parts.append(chunk.class_name)
                    if chunk.function_name:
                        parts.append(chunk.function_name)
                    symbols.append(SymbolEntry(memory_id=memory.id, qualname=".".join(parts)))

                if file_lang in EDGE_PASS_LANGUAGES:
                    edge_inputs.append((rel_path, content, file_lang))

                files_indexed += 1

            except UnicodeDecodeError:
                _skip("binary")
            except Exception as e:
                _skip("error")
                logger.warning(f"Error indexing {file_path}: {e}")

        duration = time.time() - start_time
        files_skipped = sum(skip_reasons.values())

        # v0.5 edge pass — emit + resolve + persist edges using the in-memory
        # symbol table and source already loaded above. No second filesystem
        # walk, no SQL rebuild. Best-effort: log and continue on failure.
        try:
            await _run_edge_pass(repo_path, memory_manager, edge_inputs, symbols)
        except Exception as e:
            logger.warning(f"v0.5 edge pass failed (continuing without edges): {e}")

        # Record the commit hash at time of indexing so sync_commits can diff from here.
        # v0.4 (T009): also upsert into branch_state so event-replay and the new
        # commit-aware tools can read the canonical last-indexed SHA per branch.
        try:
            context = await memory_manager.git_manager.detect_context(str(repo_path))
            memory_manager.storage_manager.set_last_indexed_commit(
                context.repo.id, context.branch.name, context.branch.commit_hash
            )
            from ..types.memory import BranchState

            parent_sha: str | None = None
            try:
                # Best-effort: find merge-base against the conventional default branch.
                for default in ("main", "master"):
                    if default == context.branch.name:
                        continue
                    parent_sha = await memory_manager.git_manager.merge_base(
                        context.branch.name, default, cwd=str(repo_path)
                    )
                    if parent_sha:
                        break
            except Exception:  # merge-base is best-effort metadata, never blocking
                parent_sha = None

            memory_manager.storage_manager.upsert_branch_state(
                BranchState(
                    repo_id=context.repo.id,
                    branch=context.branch.name,
                    last_indexed_sha=context.branch.commit_hash or None,
                    parent_sha=parent_sha,
                )
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


# v0.5: edge-emission post-pass (FR-013, FR-015, FR-017) -----------------


async def _run_edge_pass(
    repo_path: Path,
    memory_manager: "MemoryManager",
    edge_inputs: list[tuple[str, str, str]],
    symbols: list,
) -> None:
    """Extract typed edges from the files already chunked by the main loop.

    ``edge_inputs`` is a list of ``(rel_path, content, language)`` triples and
    ``symbols`` is the pre-built ``SymbolEntry`` list for every chunk just
    persisted — no second filesystem walk, no SQL rebuild.

    Best-effort: per-file failures are logged and skipped so the legacy
    chunk-only flow still produces a usable index.
    """
    if not edge_inputs or not symbols:
        return

    from ..chunking.base_chunker import RawEdge
    from ..chunking.python_ast_chunker import PythonASTChunker
    from ..core.symbol_resolver import resolve_edges
    from ..types.memory import coerce_sha

    context = await memory_manager.git_manager.detect_context(str(repo_path))
    repo_id = context.repo.id
    branch = context.branch.name
    commit_sha = coerce_sha(context.branch.commit_hash)

    from ..chunking.markdown_chunker import MarkdownChunker

    py_chunker = PythonASTChunker()
    md_chunker = MarkdownChunker()
    ts_chunker = None
    try:
        from ..chunking.tree_sitter_chunker import TreeSitterChunker

        ts_chunker = TreeSitterChunker()
    except Exception as e:
        logger.debug(f"edge pass: tree-sitter unavailable: {e}")

    all_edges: list[RawEdge] = []
    for rel_path, content, lang in edge_inputs:
        try:
            if lang == "python":
                _, edges = py_chunker.chunk_with_edges(content, rel_path)
            elif lang == "markdown":
                _, edges = md_chunker.chunk_with_edges(content, rel_path)
            elif lang in EDGE_WALKERS and ts_chunker is not None:
                _, edges = ts_chunker.chunk_with_edges(content, rel_path, lang)
            else:
                continue
            all_edges.extend(edges)
        except SyntaxError as e:
            logger.debug(f"edge pass: skipped {rel_path}: {e}")

    if not all_edges:
        return

    relations = resolve_edges(
        all_edges,
        repo_id=repo_id,
        branch=branch,
        commit_sha=commit_sha,
        symbols=symbols,
    )
    if relations:
        inserted = memory_manager.storage_manager.insert_relations(relations)
        logger.info(
            f"v0.5 edge pass: extracted {len(all_edges)} raw edges, "
            f"resolved {len(relations)}, inserted {inserted}"
        )
