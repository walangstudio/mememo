"""
SessionStart hook command.

Claude Code fires this at session open. The hook reads the project directory
from the hook JSON payload, discovers any workspace repos, searches their
memories, and returns additionalContext for the session prompt.

Usage:
    python -m mememo session-start --hook
"""

from __future__ import annotations

import asyncio
import json
import sys


async def cmd_session_start() -> None:
    """SessionStart hook: recall relevant memories across workspace repos."""
    raw = sys.stdin.read()
    try:
        hook_data = json.loads(raw)
    except json.JSONDecodeError:
        hook_data = {}

    # Claude Code sends cwd / project_dir as the working directory.
    cwd = (
        hook_data.get("cwd")
        or hook_data.get("project_dir")
        or hook_data.get("workingDirectory")
        or ""
    )

    from ..types.config import MemoConfig

    cfg = MemoConfig.from_env()

    if not cfg.hook.session_start_enabled:
        print(json.dumps({"continue": True}))
        return

    from ..server import ensure_initialized

    await ensure_initialized()

    import os

    import mememo.server as srv

    from ..core.workspace import recall_workspace

    effective_cwd = cwd or os.getcwd()

    try:
        results = await recall_workspace(
            memory_manager=srv.memory_manager,
            cwd=effective_cwd,
            query="",
            token_budget=cfg.hook.session_start_token_budget,
            min_similarity=cfg.hook.session_start_min_similarity,
            max_repos=cfg.hook.workspace_max_repos,
        )
    except Exception as exc:
        print(f"mememo session-start: recall failed: {exc}", file=sys.stderr)
        print(json.dumps({"continue": True}))
        return

    if not results:
        print(json.dumps({"continue": True}))
        return

    block = _format_session_context(results, effective_cwd)

    print(f"mememo session-start: recalled {len(results)} memories", file=sys.stderr)
    print(
        json.dumps(
            {
                "continue": True,
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": block,
                },
            }
        )
    )


def _format_session_context(results, cwd: str) -> str:
    """Format search results into a compact additionalContext block."""
    from ..core.workspace import discover_workspace

    repo_paths = discover_workspace(cwd)
    repo_set = set(repo_paths)

    lines: list[str] = ["Memories from previous sessions:"]
    for r in results:
        mem = r.memory
        # Mark global-lane and cross-repo memories with their repo path.
        from ..types.memory import GLOBAL_REPO_ID

        if mem.repo.id == GLOBAL_REPO_ID:
            prefix = "[global]"
        elif mem.repo.path and mem.repo.path not in repo_set:
            prefix = f"[{mem.repo.name}]"
        else:
            prefix = ""

        summary = mem.summary.one_line.strip()
        if prefix:
            lines.append(f"- {prefix} [{mem.content.type}] {summary}")
        else:
            lines.append(f"- [{mem.content.type}] {summary}")

    return "\n".join(lines)


def run_session_start():
    asyncio.run(cmd_session_start())
