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
import os
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

from ..chunking import ChunkerFactory
from ..chunking.ts_edges import EDGE_WALKERS
from ..types.memory import CreateMemoryParams, MemoryRelationships
from .schemas import IndexRepositoryParams, IndexRepositoryResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# Languages that emit edges: Python (AST chunker) plus every tree-sitter walker
# registered in ts_edges.EDGE_WALKERS. Informational now — chunk_file_with_edges
# dispatches by chunker type, so the edge pass no longer gates on this list — but
# kept (and tested) as the canonical "these languages produce edges" set.
EDGE_PASS_LANGUAGES: frozenset[str] = frozenset({"python", "markdown", *EDGE_WALKERS})

# Chunks accumulated before a batched flush (one git-detect + one batched embed
# + one vector add per flush).
_INDEX_BATCH = 256

# Rough chunks-per-file for the pre-index ETA estimate (this repo: ~1782/175 ≈ 10).
_EST_CHUNKS_PER_FILE = 10


def _warn_if_slow_embedding(embedder, n_files: int) -> None:
    """Loudly flag a model/device combo that makes a full index crawl, with an ETA.

    The classic "indexing hangs forever" is `MEMEMO_EMBEDDING_MODEL=qwen3` with no GPU:
    Qwen3-Embedding-0.6B embeds ~1770ms/chunk on CPU vs minilm's ~29ms (~60x), so a repo
    that indexes in ~40s on minilm takes ~1h on qwen3/CPU and looks dead. Warn (don't
    silently hang) and point at the fast path. GPU (cuda/mps) is fast, so no warning there.
    """
    from ..embeddings.embedder import MODEL_REGISTRY

    if n_files <= 0:
        return  # nothing to index (e.g. an up-to-date incremental run)
    # Only warn for a definitely-CPU embedder; absent/unknown device → stay quiet.
    if getattr(embedder, "device", "") != "cpu":
        return
    ms = MODEL_REGISTRY.get(getattr(embedder, "model_name", ""), {}).get("cpu_ms_per_chunk", 0)
    if ms <= 100:  # minilm-class — fast enough, stay quiet
        return
    est_chunks = n_files * _EST_CHUNKS_PER_FILE
    eta_min = ms * est_chunks / 1000 / 60
    fast_ms = MODEL_REGISTRY.get("minilm", {}).get("cpu_ms_per_chunk", 30)
    logger.warning(
        "mememo index: '%s' on CPU embeds ~%dms/chunk; ~%d files (~%d chunks) is roughly "
        "~%.0f min. minilm is ~%dx faster: set MEMEMO_EMBEDDING_MODEL=minilm (needs a "
        "re-index) for fast CPU indexing, or use a CUDA/MPS GPU.",
        getattr(embedder, "model_name", "?"),
        ms,
        n_files,
        est_chunks,
        eta_min,
        round(ms / fast_ms),
    )


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

        # Incremental indexing with Merkle DAG. Stage the new hashes (persist=
        # False) and commit them only after the index fully succeeds, so an
        # interrupted/crashed run never marks files indexed without their
        # memories — which would make every later incremental run skip them.
        merkle = None
        if params.incremental:
            from ..indexing.merkle_dag import MerkleDAG

            merkle = MerkleDAG(memory_manager.storage_manager.base_dir / "merkle")
            files_to_index = merkle.get_changed_files(files_to_index, persist=False)
            logger.info(f"Incremental: {len(files_to_index)} files changed since last index")

        # Flag a slow model/CPU combo up front (the usual "indexing hangs" cause).
        _warn_if_slow_embedding(memory_manager.embedder, len(files_to_index))

        # Initialize chunker factory
        chunker_factory = ChunkerFactory()

        # Index each file
        files_indexed = 0
        chunks_created = 0
        skip_reasons: dict[str, int] = {}

        # Collect the typed edges emitted alongside the chunks (one walk per
        # file, not two) plus the SymbolEntry rows, so the edge pass only has to
        # resolve + persist — no re-walk, no re-read, no SQL rebuild.
        from ..chunking.base_chunker import RawEdge
        from ..chunking.python_ast_chunker import file_path_to_module
        from ..core.symbol_resolver import SymbolEntry

        all_raw_edges: list[RawEdge] = []
        symbols: list[SymbolEntry] = []

        # Batch chunk creation: one git-detect + one batched embed + one vector
        # add per flush instead of per chunk. The old per-chunk create_memory
        # path spawned a git subprocess and ran a single-item embed for *every*
        # chunk — the cause of the multi-hour indexing hang. qualname derivation
        # needs the chunk + module, so carry that beside each pending param and
        # zip it with the memories the batch returns (order is preserved).
        pending_params: list[CreateMemoryParams] = []
        pending_meta: list[tuple] = []  # (module, chunk)

        def _skip(reason: str) -> None:
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

        async def _flush() -> None:
            nonlocal chunks_created
            if not pending_params:
                return
            # Snapshot and clear *before* the await: if create_memories_batch
            # raises, the buffers are already empty, so a later flush can't
            # re-submit these chunks and create duplicates.
            batch_params = list(pending_params)
            batch_meta = list(pending_meta)
            pending_params.clear()
            pending_meta.clear()
            # Source code legitimately contains secret-like patterns (test
            # fixtures, examples). Indexing is the same trust level as the code
            # itself, so bypass secret rejection — a single hit must never abort
            # the batch and crash the whole index.
            memories = await memory_manager.create_memories_batch(
                batch_params, cwd=str(repo_path), skip_secret_scan=True
            )
            # Track the first memory_id seen per module so we can register a
            # module-level symbol entry after processing all chunks.  IMPORTS
            # edges have source_qualname = module (e.g. "pkg.a"), but the
            # chunkers only emit class/function chunks — never a bare-module
            # chunk — so "pkg.a" would be absent from the symbol table and
            # every IMPORTS edge would be silently dropped by the resolver.
            module_first_memory: dict[str, str] = {}
            module_already_registered: set[str] = set()

            for memory, (module, chunk) in zip(memories, batch_meta):
                if chunk.qualname:
                    qualname = chunk.qualname
                else:
                    parts = [module]
                    if chunk.class_name:
                        parts.append(chunk.class_name)
                    if chunk.function_name:
                        parts.append(chunk.function_name)
                    qualname = ".".join(parts)
                symbols.append(SymbolEntry(memory_id=memory.id, qualname=qualname))

                # Record first chunk per module; detect if module itself is covered.
                if module not in module_first_memory:
                    module_first_memory[module] = memory.id
                if qualname == module:
                    module_already_registered.add(module)

            # Register a module-level symbol for every module that only has
            # class/function children but no bare-module chunk — this allows the
            # resolver to find the source of IMPORTS edges.
            for mod, first_id in module_first_memory.items():
                if mod not in module_already_registered:
                    symbols.append(SymbolEntry(memory_id=first_id, qualname=mod))

            chunks_created += len(memories)

        total = len(files_to_index)
        for i, file_path in enumerate(files_to_index):
            try:
                content = file_path.read_text(encoding="utf-8")
                # Chunk by rel_path (not the absolute path): chunk_with_edges
                # derives the edge source qualname's module from this path, and
                # it must match the symbol table built below (also rel-path based)
                # or every edge would be dropped as having an unknown source.
                rel_path = str(file_path.relative_to(repo_path)).replace("\\", "/")
                chunks, raw_edges = chunker_factory.chunk_file_with_edges(content, rel_path)
                if not chunks:
                    _skip("empty_chunks")
                    continue

                module = file_path_to_module(rel_path)

                for chunk in chunks:
                    pending_params.append(
                        CreateMemoryParams(
                            content=chunk.text,
                            type="code_snippet",
                            language=chunk.language,
                            file_path=rel_path,
                            line_range=(
                                (chunk.start_line, chunk.end_line) if chunk.start_line else None
                            ),
                            function_name=chunk.function_name,
                            class_name=chunk.class_name,
                            docstring=chunk.docstring,
                            decorators=chunk.decorators,
                            parent_class=chunk.parent_class,
                            attributes=chunk.attributes,
                            tags=["indexed", "repository"],
                            relationships=MemoryRelationships(),
                        )
                    )
                    pending_meta.append((module, chunk))

                all_raw_edges.extend(raw_edges)

                files_indexed += 1

            except UnicodeDecodeError:
                _skip("binary")
                continue
            except Exception as e:
                _skip("error")
                logger.warning(f"Error indexing {file_path}: {e}")
                continue

            # Flush outside the per-file try: a batch-write failure is a real
            # storage error, not a skippable per-file problem. Letting it
            # propagate aborts the index via the outer handler, so merkle.commit()
            # never runs and the next incremental run re-detects everything —
            # instead of silently swallowing it and leaving stale Merkle state.
            if len(pending_params) >= _INDEX_BATCH:
                await _flush()
            if (i + 1) % 100 == 0:
                logger.info(
                    f"Indexing progress: {i + 1}/{total} files, "
                    f"{chunks_created + len(pending_params)} chunks"
                )

        await _flush()

        duration = time.time() - start_time
        files_skipped = sum(skip_reasons.values())

        # v0.5 edge pass — resolve + persist the edges collected during the main
        # loop against the in-memory symbol table. The walk already happened once
        # per file above. Best-effort: log and continue on failure.
        try:
            await _resolve_and_persist_edges(repo_path, memory_manager, all_raw_edges, symbols)
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

        # Index succeeded — now it's safe to persist change-detection state.
        if merkle is not None:
            merkle.commit()

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

# Skip obviously-binary files by extension and skip anything huge. Mirrors the
# ignore lists GitNexus/Graphify use so we never read/chunk a media blob or a
# generated megafile.
_MAX_FILE_BYTES = 1_500_000
_BINARY_EXTS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".webp",
        ".tiff",
        ".pdf",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".o",
        ".a",
        ".lib",
        ".class",
        ".jar",
        ".pyc",
        ".pyo",
        ".wasm",
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".mov",
        ".mkv",
        ".flac",
        ".ogg",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".eot",
        ".bin",
        ".dat",
        ".db",
        ".sqlite",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
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

    Uses os.walk with in-place dir pruning so ignored trees (``target/``,
    ``.git/``, ``node_modules/`` …) are never *descended* — the old glob walked
    them fully and filtered after, which on a Rust ``target/`` (100k+ files) cost
    minutes just to enumerate. Patterns are matched on the basename (``**/*.py``
    → ``*.py``), preserving the extension-filter intent of the defaults.

    Args:
        repo_path: Repository root path
        patterns: List of glob patterns
        max_files: Maximum files to return
        ignored_dirs: Directory names to exclude (defaults to common ignored dirs)

    Returns:
        List of matching file paths
    """
    skip_dirs = ignored_dirs if ignored_dirs is not None else _DEFAULT_IGNORED_DIRS
    pat_names = [p.rsplit("/", 1)[-1] for p in patterns]
    matching: list[Path] = []

    for root, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            if Path(fn).suffix.lower() in _BINARY_EXTS:
                continue
            if not any(fnmatch(fn, pat) for pat in pat_names):
                continue
            fpath = Path(root) / fn
            try:
                if fpath.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            matching.append(fpath)
            if len(matching) >= max_files:
                return sorted(matching)[:max_files]

    return sorted(matching)[:max_files]


# v0.5: edge-emission post-pass (FR-013, FR-015, FR-017) -----------------


async def _resolve_and_persist_edges(
    repo_path: Path,
    memory_manager: "MemoryManager",
    raw_edges: list,
    symbols: list,
) -> None:
    """Resolve the edges collected during the main loop and persist relations.

    The chunk walk already emitted these edges in the same pass that built the
    chunks, so this only resolves ``raw_edges`` against the in-memory symbol
    table and inserts the resulting relations — no second walk, no SQL rebuild.
    """
    if not raw_edges or not symbols:
        return

    from ..core.symbol_resolver import resolve_edges
    from ..types.memory import coerce_sha

    context = await memory_manager.git_manager.detect_context(str(repo_path))
    repo_id = context.repo.id
    branch = context.branch.name
    commit_sha = coerce_sha(context.branch.commit_hash)

    relations = resolve_edges(
        raw_edges,
        repo_id=repo_id,
        branch=branch,
        commit_sha=commit_sha,
        symbols=symbols,
    )
    if relations:
        inserted = memory_manager.storage_manager.insert_relations(relations)
        logger.info(
            f"v0.5 edge pass: collected {len(raw_edges)} raw edges, "
            f"resolved {len(relations)}, inserted {inserted}"
        )
