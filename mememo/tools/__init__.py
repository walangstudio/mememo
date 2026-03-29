"""
MCP tools for mememo.

15 tools for memory operations:
- store_memory: Store code snippets, decisions, analysis, conversation notes
- store_decision: Store structured architectural decisions
- capture: Passive memory capture via LLM extraction (or passthrough self-extract)
- retrieve_memory: Get memory by ID
- search_similar: Semantic similarity search (excludes stale by default)
- recall_context: Multi-type semantic search across persistent memory types
- recent_context: Return N most recent memories
- list_memories: List with filters (excludes stale by default)
- summarize_context: Hierarchical summaries (optionally persisted)
- end_session: Store session summary as conversation memory
- delete_memory: Delete by ID
- index_repository: Batch indexing (records last indexed commit)
- sync_commits: Patch memories to reflect new commits
- check_memory: Get statistics
- refresh_memory: Update existing memory
"""

from .capture import capture
from .check_memory import check_memory
from .cleanup_memory import cleanup_memory
from .delete_memory import delete_memory
from .end_session import end_session
from .index_repository import index_repository
from .list_memories import list_memories
from .manage_skill import manage_skill
from .recall_context import recall_context
from .recent_context import recent_context
from .refresh_memory import refresh_memory
from .retrieve_memory import retrieve_memory
from .search_similar import search_similar
from .store_decision import store_decision
from .store_memory import store_memory
from .summarize_context import summarize_context
from .sync_commits import sync_commits

__all__ = [
    "store_memory",
    "store_decision",
    "capture",
    "retrieve_memory",
    "search_similar",
    "recall_context",
    "recent_context",
    "list_memories",
    "summarize_context",
    "end_session",
    "delete_memory",
    "index_repository",
    "sync_commits",
    "check_memory",
    "refresh_memory",
    "manage_skill",
    "cleanup_memory",
]
