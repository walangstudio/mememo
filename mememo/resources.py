"""MCP read-only resources (T031 / FR-026, FR-027).

Each resource returns a small JSON payload (≤ 4 KB) summarising part of
the memory store. List-shaped resources truncate to a configurable cap
and include a ``truncated`` marker so callers know they should query
the relevant tool for the full set.

Resources are registered against the FastMCP server in server.py; this
module defines the pure-Python implementations so they're independently
testable.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core.memory_manager import MemoryManager


MAX_PAYLOAD_BYTES = 4096
MAX_LIST_ITEMS = 50


def _bound(payload: dict, max_items_key: str | None = None) -> dict:
    """Truncate list-valued fields until the JSON payload fits the 4 KB cap.

    If ``max_items_key`` is provided, that list is shortened first; otherwise
    the longest list found in the payload is trimmed.
    """
    encoded = json.dumps(payload)
    if len(encoded) <= MAX_PAYLOAD_BYTES:
        return payload

    def _shrink(key: str) -> bool:
        if key in payload and isinstance(payload[key], list) and payload[key]:
            payload[key] = payload[key][: max(1, len(payload[key]) // 2)]
            payload["truncated"] = True
            return True
        return False

    while len(json.dumps(payload)) > MAX_PAYLOAD_BYTES:
        if max_items_key and _shrink(max_items_key):
            continue
        # Fall back: shrink the longest list.
        longest = None
        for k, v in payload.items():
            if isinstance(v, list) and (longest is None or len(v) > len(payload[longest])):
                longest = k
        if longest is None or not _shrink(longest):
            break
    return payload


def repo_stats(memory_manager: MemoryManager, repo_id: str) -> str:
    """``mememo://repo/{id}/stats`` — counts + last-indexed SHA + stale fraction."""
    conn = memory_manager.storage_manager.conn
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM memories WHERE repo_id = ?", (repo_id,)
    ).fetchone()["n"]
    stale = conn.execute(
        "SELECT COUNT(*) AS n FROM memories WHERE repo_id = ? AND stale = 1",
        (repo_id,),
    ).fetchone()["n"]
    relations = conn.execute(
        "SELECT COUNT(*) AS n FROM relations WHERE repo_id = ?", (repo_id,)
    ).fetchone()["n"]
    communities = conn.execute(
        "SELECT COUNT(DISTINCT community) AS n FROM relations "
        "WHERE repo_id = ? AND community IS NOT NULL",
        (repo_id,),
    ).fetchone()["n"]
    branches_rows = conn.execute(
        "SELECT branch, last_indexed_sha FROM branch_state WHERE repo_id = ?",
        (repo_id,),
    ).fetchall()
    branches = [
        {"branch": r["branch"], "last_indexed_sha": r["last_indexed_sha"]} for r in branches_rows
    ]
    payload = {
        "repo_id": repo_id,
        "memories_total": total,
        "memories_stale": stale,
        "stale_fraction": round(stale / total, 3) if total else 0.0,
        "relations_total": relations,
        "community_count": communities,
        "branches": branches,
    }
    return json.dumps(_bound(payload, max_items_key="branches"))


def repo_stale(memory_manager: MemoryManager, repo_id: str) -> str:
    """``mememo://repo/{id}/stale`` — list of stale memories with risk_grade."""
    conn = memory_manager.storage_manager.conn
    rows = conn.execute(
        "SELECT id, file_path, function_name, class_name, risk_grade, stale_reason "
        "FROM memories WHERE repo_id = ? AND stale = 1 "
        "ORDER BY updated_at DESC LIMIT ?",
        (repo_id, MAX_LIST_ITEMS),
    ).fetchall()
    items = [dict(r) for r in rows]
    payload = {"repo_id": repo_id, "count": len(items), "items": items}
    return json.dumps(_bound(payload, max_items_key="items"))


def branch_summary(memory_manager: MemoryManager, repo_id: str, branch: str) -> str:
    """``mememo://repo/{id}/branch/{name}/summary`` — per-branch counts + SHAs."""
    conn = memory_manager.storage_manager.conn
    counts = conn.execute(
        "SELECT COUNT(*) AS n FROM memories WHERE repo_id = ? AND branch_name = ?",
        (repo_id, branch),
    ).fetchone()
    rel_count = conn.execute(
        "SELECT COUNT(*) AS n FROM relations WHERE repo_id = ? AND branch = ?",
        (repo_id, branch),
    ).fetchone()["n"]
    state = conn.execute(
        "SELECT last_indexed_sha, parent_sha FROM branch_state " "WHERE repo_id = ? AND branch = ?",
        (repo_id, branch),
    ).fetchone()
    event_count = conn.execute(
        "SELECT COUNT(*) AS n FROM memory_events WHERE branch = ?", (branch,)
    ).fetchone()["n"]
    payload = {
        "repo_id": repo_id,
        "branch": branch,
        "memories": counts["n"],
        "relations": rel_count,
        "events": event_count,
        "last_indexed_sha": state["last_indexed_sha"] if state else None,
        "parent_sha": state["parent_sha"] if state else None,
    }
    return json.dumps(_bound(payload))


def community_summary(memory_manager: MemoryManager, repo_id: str, community_id: int) -> str:
    """``mememo://repo/{id}/community/{cid}`` — member memories + top-degree nodes."""
    conn = memory_manager.storage_manager.conn
    member_ids_rows = conn.execute(
        "SELECT DISTINCT source_memory_id AS mid FROM relations "
        "WHERE repo_id = ? AND community = ? "
        "UNION "
        "SELECT DISTINCT target_memory_id AS mid FROM relations "
        "WHERE repo_id = ? AND community = ? AND target_memory_id IS NOT NULL",
        (repo_id, community_id, repo_id, community_id),
    ).fetchall()
    member_ids = [r["mid"] for r in member_ids_rows]
    # Top-degree: count edges per node within the community.
    degree_rows = conn.execute(
        "SELECT mid, SUM(d) AS deg FROM ("
        "  SELECT source_memory_id AS mid, COUNT(*) AS d FROM relations "
        "  WHERE repo_id = ? AND community = ? GROUP BY source_memory_id "
        "  UNION ALL "
        "  SELECT target_memory_id AS mid, COUNT(*) AS d FROM relations "
        "  WHERE repo_id = ? AND community = ? AND target_memory_id IS NOT NULL "
        "  GROUP BY target_memory_id"
        ") GROUP BY mid ORDER BY deg DESC LIMIT 10",
        (repo_id, community_id, repo_id, community_id),
    ).fetchall()
    top_degree = [{"memory_id": r["mid"], "degree": r["deg"]} for r in degree_rows]
    payload = {
        "repo_id": repo_id,
        "community_id": community_id,
        "member_count": len(member_ids),
        "top_degree": top_degree,
        "members": member_ids[:MAX_LIST_ITEMS],
    }
    return json.dumps(_bound(payload, max_items_key="members"))
