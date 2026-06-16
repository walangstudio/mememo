"""Optional tool-group gating to shrink the exposed MCP surface.

Every registered tool's schema is sent to the model on every turn, so a large
surface is a standing token cost. Grouping lets a deployment expose only the
tools it uses:

- ``MEMEMO_TOOLS`` — comma-separated allowlist of groups to expose ONLY those
  (e.g. ``core,comprehension``).
- ``MEMEMO_DISABLE_TOOLS`` — comma-separated denylist of groups to drop
  (e.g. ``skills,diagrams``).
- Neither set — all groups (back-compat default).

``MEMEMO_TOOLS`` takes precedence; ``MEMEMO_DISABLE_TOOLS`` then subtracts from
the result. Unknown group names are ignored. If the resulting set is empty
(e.g. a typo'd allowlist), every group is kept — never silently expose nothing.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# tool name -> group. Must stay in sync with the @mcp.tool() functions in
# server.py; tests/test_tool_groups.py asserts there is no drift.
TOOL_GROUPS: dict[str, str] = {
    # core — memory store / recall / session
    "store_memory": "core",
    "batch_store": "core",
    "store_decision": "core",
    "retrieve_memory": "core",
    "search_similar": "core",
    "recall_context": "core",
    "recent_context": "core",
    "list_memories": "core",
    "delete_memory": "core",
    "summarize_context": "core",
    "check_memory": "core",
    "cleanup_memory": "core",
    "refresh_memory": "core",
    "end_session": "core",
    "capture": "core",
    # index — code indexing, the typed graph, git time-travel
    "index_repository": "index",
    "detect_changes": "index",
    "sync_commits": "index",
    "graph_neighbors": "index",
    "graph_impact": "index",
    "graph_path": "index",
    "cypher_query": "index",
    "recall_at_commit": "index",
    "merge_branch": "index",
    # diagrams — Mermaid generation off the graph
    "generate_diagram": "diagrams",
    # comprehension — LLM/passthrough repo understanding
    "ask": "comprehension",
    "overview": "comprehension",
    "generate_wiki": "comprehension",
    "explore": "comprehension",
    "project_prompt": "comprehension",
    "enrich_docstrings": "comprehension",
    # skills — distilled-skill library
    "manage_skill": "skills",
    "curate_skills": "skills",
}

ALL_GROUPS: frozenset[str] = frozenset(TOOL_GROUPS.values())


def _parse(val: str | None) -> set[str]:
    if not val:
        return set()
    return {p.strip().lower() for p in val.split(",") if p.strip()}


def enabled_groups(env: dict | None = None) -> set[str]:
    """Resolve the set of enabled groups from the environment."""
    env = env if env is not None else os.environ
    allow = _parse(env.get("MEMEMO_TOOLS")) & ALL_GROUPS
    deny = _parse(env.get("MEMEMO_DISABLE_TOOLS")) & ALL_GROUPS
    # What's permitted before the denylist: the allowlist, or everything when no
    # valid allowlist was given (so a typo'd MEMEMO_TOOLS doesn't hide all tools).
    base = allow if allow else set(ALL_GROUPS)
    groups = base - deny
    # If the denylist cancels everything, fall back to the permitted base (the
    # allowlist) rather than to ALL — a contradictory allow+deny must not blow the
    # surface wide open, and the server is never left with no tools.
    return groups or base


def disabled_tool_names(env: dict | None = None) -> list[str]:
    """Tool names whose group is not enabled, given the environment."""
    groups = enabled_groups(env)
    return [name for name, group in TOOL_GROUPS.items() if group not in groups]


def apply_tool_filter(mcp, env: dict | None = None) -> list[str]:
    """Remove disabled tools from the FastMCP instance. Returns removed names."""
    names = disabled_tool_names(env)
    if not names:
        return []
    # FastMCP 3 deprecated mcp.remove_tool in favour of local_provider.remove_tool;
    # prefer the new path, fall back for older versions. Resolve defensively: if a
    # future version exposes neither, skip filtering rather than crash startup.
    provider = getattr(mcp, "local_provider", None)
    remover = getattr(provider, "remove_tool", None) or getattr(mcp, "remove_tool", None)
    if remover is None:
        logger.warning("tool groups: no remove_tool API on this FastMCP — tool filtering skipped")
        return []
    removed: list[str] = []
    for name in names:
        try:
            remover(name)
            removed.append(name)
        except Exception as exc:  # tool already absent / API drift — non-fatal
            logger.debug("tool filter: could not remove %s: %s", name, exc)
    if removed:
        logger.info(
            "tool groups: exposing %s; removed %d tool(s)",
            ",".join(sorted(enabled_groups(env))),
            len(removed),
        )
    return removed
