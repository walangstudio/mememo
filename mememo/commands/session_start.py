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

    import os

    from ..types.config import MemoConfig

    cfg = MemoConfig.from_env()

    # Opt-in: keep the current repo indexed without an explicit trigger. Spawns a
    # detached `mememo index` so it never blocks session open. Runs independently
    # of recall (a repo with no memories yet is exactly the one to index).
    if cfg.hook.auto_index_on_session_start:
        _maybe_background_index(cfg, cwd or os.getcwd())

    # Opt-in: autonomously keep the distilled-skill library lean (Hermes-style periodic
    # curator) without an external cron. Detached + interval-locked like auto-index.
    if cfg.hook.auto_curate_on_session_start:
        _maybe_background_curate(cfg)

    if not cfg.hook.session_start_enabled:
        print(json.dumps({"continue": True}))
        return

    from ..server import ensure_initialized

    await ensure_initialized()

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


def _spawn_detached(argv: list[str], stderr=None) -> None:
    """Start a process that outlives this short-lived hook process."""
    import subprocess

    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": stderr if stderr is not None else subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP. NOT DETACHED_PROCESS: a
        # console-less parent makes every console child (git.exe per hook
        # request) pop a NEW VISIBLE cmd window; a hidden console is inherited
        # silently.
        kwargs["creationflags"] = 0x08000000 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)


def _maybe_background_index(cfg, cwd: str, spawn=_spawn_detached) -> None:
    """Background-index the current repo if one hasn't run recently.

    Best-effort and fully guarded: any failure is swallowed so a flaky spawn
    never breaks session open. A per-repo lock file (TTL = auto_index_min_interval)
    prevents every concurrent session from kicking off its own index.
    """
    import os
    import time
    from pathlib import Path

    try:
        from ..core.workspace import discover_workspace

        repos = discover_workspace(cwd) or []
    except Exception:
        repos = []
    if not repos:
        # Only fall back to cwd if it's actually a git repo — otherwise we'd
        # index an arbitrary directory (home / workspace root) under the global
        # lane, polluting it and burying repo-scoped recall.
        if cwd and (Path(cwd) / ".git").exists():
            repos = [cwd]
        else:
            return

    repo = repos[0]

    # Claim the slot atomically *before* spawning so two sessions opening at once
    # don't both kick off an index. O_EXCL create is the only atomic step: the
    # winner creates the file; a loser that sees a fresh lock skips. A stale lock
    # (older than the TTL) is reclaimed. The bounded loop avoids the TOCTOU where
    # both sessions unlink a stale lock and both then create — only one create
    # wins, the other re-checks and sees the now-fresh lock.
    lock = None
    try:
        from ..utils.hashing import hash_path

        lock_dir = Path(cfg.storage.base_dir).expanduser() / "autoindex"
        lock_dir.mkdir(parents=True, exist_ok=True)
        candidate = lock_dir / f"{hash_path(repo)}.lock"
        ttl = cfg.hook.auto_index_min_interval_minutes * 60
        for _ in range(3):
            try:
                with open(candidate, "x", encoding="utf-8") as fh:
                    fh.write(repo)
                lock = candidate
                break
            except FileExistsError:
                try:
                    age = time.time() - candidate.stat().st_mtime
                except OSError:
                    continue  # vanished between create and stat — retry
                if age < ttl:
                    return  # a recent run (possibly a racing session) owns it
                try:
                    candidate.unlink()  # stale — reclaim and retry the create
                except OSError:
                    pass
    except Exception:
        lock = None  # locking is best-effort; proceed unguarded

    argv = [sys.executable, "-m", "mememo", "index", repo, "--quiet"]
    if lock is not None:
        # Hand the lock to the child so it releases it on failure (the child can
        # detect its own crash; the parent, fire-and-forget, cannot).
        argv += ["--autoindex-lock", str(lock)]
    try:
        spawn(argv)
        print(
            f"mememo session-start: background indexing {os.path.basename(repo)}", file=sys.stderr
        )
    except Exception as exc:
        if lock is not None:
            try:
                lock.unlink()  # release so a later session can retry
            except OSError:
                pass
        print(f"mememo session-start: auto-index spawn failed: {exc}", file=sys.stderr)


def _maybe_background_curate(cfg, spawn=_spawn_detached) -> None:
    """Background-run ``curate-skills`` if one hasn't run within the interval.

    Skills are GLOBAL (not per-repo), so a single interval lock guards every session.
    Best-effort: any failure is swallowed so it never breaks session open. The detached
    child only does the deterministic prunes (exact dupes + optional stale-unused) — the
    near-duplicate merge needs a host model and still happens when curate_skills is
    called interactively.
    """
    import time
    from pathlib import Path

    lock = None
    try:
        lock_dir = Path(cfg.storage.base_dir).expanduser() / "autocurate"
        lock_dir.mkdir(parents=True, exist_ok=True)
        candidate = lock_dir / "curate.lock"
        ttl = cfg.hook.auto_curate_min_interval_hours * 3600
        # Atomic O_EXCL claim; reclaim a stale lock. Bounded retry avoids the
        # two-sessions-both-unlink-then-create TOCTOU (mirrors _maybe_background_index).
        for _ in range(3):
            try:
                with open(candidate, "x", encoding="utf-8") as fh:
                    fh.write(str(time.time()))
                lock = candidate
                break
            except FileExistsError:
                try:
                    age = time.time() - candidate.stat().st_mtime
                except OSError:
                    continue
                if age < ttl:
                    return  # a recent run owns the slot
                try:
                    candidate.unlink()
                except OSError:
                    pass
    except Exception:
        lock = None  # locking is best-effort

    argv = [sys.executable, "-m", "mememo", "curate-skills", "--apply"]
    if cfg.hook.auto_curate_stale_unused_days > 0:
        argv += ["--stale-days", str(cfg.hook.auto_curate_stale_unused_days)]
    if lock is not None:
        # Hand the lock to the child so it releases on its OWN failure (a crashed
        # child would otherwise hold the slot for the full interval). Success keeps it.
        argv += ["--lock", str(lock)]
    try:
        spawn(argv)
        print("mememo session-start: background curating skills", file=sys.stderr)
    except Exception as exc:
        if lock is not None:
            try:
                lock.unlink()  # release so a later session can retry
            except OSError:
                pass
        print(f"mememo session-start: auto-curate spawn failed: {exc}", file=sys.stderr)


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
