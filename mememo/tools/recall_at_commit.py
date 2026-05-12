"""recall_at_commit MCP tool (FR-012).

Time-travel semantic search: resolve a target git SHA to its commit
timestamp, replay memory_events up to that timestamp to compute the
alive memory set, then run a normal FAISS search and filter results
to that set.

No materialised snapshots — reconstruction is on-the-fly per call
(FR-005). For repos with many edits, future optimisation could cache
alive sets per (branch, ts) but v0.4 keeps it simple.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import re

from pydantic import BaseModel, Field, field_validator

from ..types import SearchParams, SearchResult

_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")
# Refs accept letters, digits, /, ., :, ~, ^, @, {}, -, and _; must NOT start with '-'
# (git option injection guard). Cap at 200 chars to bound the attack surface.
_REF_RE = re.compile(r"^(?!-)[\w/.:~^@{}-]{1,200}$")

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


class RecallAtCommitParams(BaseModel):
    query: str = Field(min_length=1)
    sha: str = Field(description="Target git SHA (4-40 hex chars) to recall as-of")
    repo_path: str = Field(description="Working directory inside the target git repo")
    top_k: int = Field(default=5, ge=1, le=100)
    min_similarity: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("sha")
    @classmethod
    def _validate_sha(cls, v: str) -> str:
        # Hardening: prevents git option-injection via sha='--upload-pack=...'
        # (security audit 2026-05-13).
        if not _SHA_RE.match(v):
            raise ValueError(f"sha must be 4-40 hex chars; got {v!r}")
        return v


class RecallAtCommitResponse(BaseModel):
    success: bool
    message: str
    sha: str | None = None
    target_ts: int | None = None
    alive_count: int = 0
    results: list[SearchResult] = Field(default_factory=list)


async def _commit_ts(memory_manager: "MemoryManager", sha: str, cwd: str) -> int | None:
    """Resolve a SHA to its committer-timestamp (epoch seconds)."""
    # Defence-in-depth: even with the Pydantic validator above, append '--' to
    # force git to treat sha as a revision not an option.
    try:
        out = await memory_manager.git_manager._exec_git(
            "log", ["-1", "--format=%ct", sha, "--"], cwd
        )
    except RuntimeError as e:
        logger.debug("git log -1 --format=%%ct %s failed: %s", sha, e)
        return None
    out = out.strip()
    return int(out) if out.isdigit() else None


async def recall_at_commit(
    params: RecallAtCommitParams, memory_manager: "MemoryManager"
) -> RecallAtCommitResponse:
    repo_path = Path(params.repo_path)
    if not repo_path.exists() or not repo_path.is_dir():
        return RecallAtCommitResponse(
            success=False, message=f"Repository path not found: {params.repo_path}"
        )

    try:
        context = await memory_manager.git_manager.detect_context(str(repo_path))
    except RuntimeError as e:
        return RecallAtCommitResponse(success=False, message=str(e))

    target_ts = await _commit_ts(memory_manager, params.sha, str(repo_path))
    if target_ts is None:
        return RecallAtCommitResponse(
            success=False,
            message=f"Cannot resolve commit timestamp for {params.sha}",
            sha=params.sha,
        )

    alive = memory_manager.storage_manager.alive_memory_ids_at_ts(
        target_ts, repo_id=context.repo.id, branch=context.branch.name
    )
    if not alive:
        return RecallAtCommitResponse(
            success=True,
            message=f"No memories alive at {params.sha[:8]}",
            sha=params.sha,
            target_ts=target_ts,
            alive_count=0,
            results=[],
        )

    # Run normal semantic search, then filter to alive set.
    raw = await memory_manager.search_similar(
        SearchParams(query=params.query, top_k=max(params.top_k * 3, 20),
                     min_similarity=params.min_similarity),
        cwd=str(repo_path),
    )
    filtered = [r for r in raw if r.memory.id in alive][: params.top_k]

    return RecallAtCommitResponse(
        success=True,
        message=(
            f"Recalled {len(filtered)} memories as-of {params.sha[:8]} "
            f"({len(alive)} alive, {len(raw)} pre-filter hits)"
        ),
        sha=params.sha,
        target_ts=target_ts,
        alive_count=len(alive),
        results=filtered,
    )
