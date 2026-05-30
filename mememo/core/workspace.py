"""
Workspace discovery and cross-repo recall for the SessionStart hook.

discover_workspace: finds git repos reachable from a directory in one level.
recall_workspace: embeds a query once and searches across all discovered repos
    plus the GLOBAL_REPO_ID lane, merging results by similarity.
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def discover_workspace(cwd: str) -> list[str]:
    """Return resolved paths of git repos reachable from *cwd* in one level.

    If *cwd* itself is a git repo, return ``[cwd]`` immediately (no scan).
    Otherwise scan immediate children for ``.git`` dirs and merge any paths
    listed under ``projects:`` in ``.mememo/workspace.yaml``.
    Caps at ``config.hook.workspace_max_repos`` (loaded here to keep the
    signature simple; callers that already have cfg can use
    ``_discover_workspace_capped`` directly).

    Returns absolute, normalised path strings.
    """
    from ..types.config import MemoConfig
    from .project_config import load_workspace_config

    cfg = MemoConfig.from_env()
    return _discover_workspace_capped(cwd, cfg.hook.workspace_max_repos, load_workspace_config)


def _discover_workspace_capped(
    cwd: str,
    max_repos: int,
    load_workspace_config_fn,
) -> list[str]:
    """Core discovery logic with injected loader (testable without config)."""
    root = Path(cwd).resolve()

    # If cwd is itself a git repo, short-circuit.
    if (root / ".git").exists():
        return [str(root)]

    found: list[str] = []

    try:
        with os.scandir(root) as it:
            for entry in it:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if (Path(entry.path) / ".git").exists():
                    found.append(str(Path(entry.path).resolve()))
                if len(found) >= max_repos:
                    break
    except PermissionError:
        logger.warning("workspace: cannot scan %s (permission denied)", root)

    # Merge workspace.yaml entries
    ws_cfg = load_workspace_config_fn(root)
    extra_paths: list = ws_cfg.get("projects") or []
    for raw in extra_paths:
        p = Path(str(raw))
        if not p.is_absolute():
            p = (root / p).resolve()
        else:
            p = p.resolve()
        ps = str(p)
        if ps not in found and p.is_dir():
            found.append(ps)
        if len(found) >= max_repos:
            break

    return found[:max_repos]


async def recall_workspace(
    memory_manager,
    cwd: str,
    query: str,
    token_budget: int,
    min_similarity: float,
    max_repos: int,
) -> list:
    """Search memories across all repos discovered from *cwd* plus GLOBAL_REPO_ID.

    Embeds *query* once, then iterates over each (repo_id, branch) pair and
    the global lane, merging + re-ranking by similarity.  If *query* is empty,
    falls back to ``find_memories`` (recent) per repo.

    Returns a flat list of ``SearchResult`` objects sorted by similarity desc,
    under *token_budget*.
    """
    from ..types.memory import MemoryFilters
    from ..utils.token_counter import count_tokens
    from .identity import GLOBAL_REPO_ID, resolve_project_id
    from .project_config import load_project_config

    repo_paths = _discover_workspace_capped(
        cwd,
        max_repos,
        __import__(
            "mememo.core.project_config", fromlist=["load_workspace_config"]
        ).load_workspace_config,
    )

    # Resolve repo_ids for each discovered path; also detect current branch.
    async def _repo_id_and_branch(path: str):
        try:
            remote_url = await memory_manager.git_manager.get_remote_url(path)
            project_cfg = load_project_config(path)
            repo_id = resolve_project_id(path, remote_url, project_cfg)
            branch = await memory_manager.git_manager.get_current_branch(path)
            return repo_id, branch
        except Exception as exc:
            logger.debug("workspace: cannot resolve identity for %s: %s", path, exc)
            return None, None

    per_repo: list[tuple[str, str]] = []
    for rp in repo_paths:
        rid, branch = await _repo_id_and_branch(rp)
        if rid and branch:
            per_repo.append((rid, branch))

    # Always include the global lane.
    per_repo.append((GLOBAL_REPO_ID, "main"))

    all_results: list = []

    if query.strip():
        query_embedding = memory_manager.embedder.embed_query(query)

        for repo_id, branch in per_repo:
            try:
                vi = memory_manager._get_vector_index(repo_id, branch)
                distances, memory_ids = vi.search(
                    query_embedding=query_embedding.tolist(),
                    top_k=10,
                )
                candidates: list[tuple[str, float]] = []
                for memory_id, distance in zip(memory_ids, distances):
                    similarity = math.exp(-distance)
                    if similarity >= min_similarity:
                        candidates.append((memory_id, similarity))

                if not candidates:
                    continue

                from ..types.memory import BranchContext, GitContext, RepoContext

                _ctx = GitContext(
                    repo=RepoContext(id=repo_id, name="", path="", remote_url=None),
                    branch=BranchContext(name=branch, commit_hash=""),
                )
                memories = await memory_manager.storage_manager.load_memories(
                    [mid for mid, _ in candidates],
                    _ctx,
                    content_types={"decision", "analysis", "context", "summary", "reference"},
                )
                sim_by_id = dict(candidates)
                for mem in memories:
                    if mem.metadata.stale:
                        continue
                    from ..types.memory import SearchResult

                    all_results.append(SearchResult(memory=mem, similarity=sim_by_id[mem.id]))
            except Exception as exc:
                logger.debug(
                    "workspace: search failed for repo=%s branch=%s: %s", repo_id, branch, exc
                )
    else:
        # Empty query: fall back to recent memories per repo
        for repo_id, branch in per_repo:
            try:
                from ..types.memory import BranchContext, GitContext, RepoContext

                _ctx = GitContext(
                    repo=RepoContext(id=repo_id, name="", path="", remote_url=None),
                    branch=BranchContext(name=branch, commit_hash=""),
                )
                filters = MemoryFilters(
                    repo_id=repo_id,
                    branch=branch,
                    include_stale=False,
                    limit=10,
                    sort_by="date",
                )
                memories = await memory_manager.storage_manager.find_memories(filters, _ctx)
                for mem in memories:
                    from ..types.memory import SearchResult

                    all_results.append(SearchResult(memory=mem, similarity=0.0))
            except Exception as exc:
                logger.debug("workspace: find_memories failed for repo=%s: %s", repo_id, exc)

    # Re-rank by similarity desc, de-duplicate by memory id.
    seen: set[str] = set()
    merged: list = []
    for r in sorted(all_results, key=lambda r: r.similarity, reverse=True):
        if r.memory.id not in seen:
            seen.add(r.memory.id)
            merged.append(r)

    # Trim to token_budget.
    trimmed: list = []
    used = 0
    for r in merged:
        tok = count_tokens(r.memory.content.text)
        if used + tok > token_budget:
            continue
        trimmed.append(r)
        used += tok

    return trimmed
