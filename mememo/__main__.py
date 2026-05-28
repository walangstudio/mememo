"""CLI entry point for mememo.

Usage:
    python -m mememo                        # Run MCP server
    python -m mememo --version              # Show version
    python -m mememo capture --hook         # Stop hook: auto-capture
    python -m mememo inject --hook          # UserPromptSubmit: inject context
    python -m mememo pre-tool --hook        # PreToolUse: related-memory block
    python -m mememo install-git-hooks --repo-path <p> [--force] [--with-pretool]
    python -m mememo migrate-worktrees --repo-path <p> [--dry-run]
    python -m mememo merge-branch --repo-path <p> --source <b> --target <b> [--merge-sha <sha>]
    python -m mememo sync-commits --repo-path <p>
    python -m mememo serve [--port 5757]
"""

from __future__ import annotations

import os
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("mememo")
except PackageNotFoundError:
    __version__ = "unknown"


# --- subcommand handlers ----------------------------------------------------


def _cmd_install_git_hooks(args: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="mememo install-git-hooks")
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--force", action="store_true")
    ap.add_argument(
        "--with-pretool",
        action="store_true",
        help="also register PreToolUse hook in .claude/settings.json",
    )
    ns = ap.parse_args(args)

    from .hooks.installer import install_git_hooks, register_claude_pretool_hook

    result = install_git_hooks(ns.repo_path, force=ns.force)
    print(result.report())
    ok = result.ok
    if ns.with_pretool:
        pre = register_claude_pretool_hook(ns.repo_path, force=ns.force)
        print(f"pre-tool: {pre.get('status')} ({pre.get('settings_path')})")
        if pre.get("status") == "error":
            ok = False
    return 0 if ok else 1


def _cmd_serve(args: list[str]) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="mememo serve")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5757)
    ns = ap.parse_args(args)

    try:
        from .web.app import run as _serve
    except ImportError as e:
        sys.stderr.write(
            "mememo serve requires the optional 'web' extras. Install with:\n"
            "    pip install 'mememo[web]'\n"
            f"(underlying error: {e})\n"
        )
        return 1

    _serve(host=ns.host, port=ns.port)
    return 0


def _cmd_migrate_worktrees(args: list[str]) -> int:
    import argparse
    import asyncio
    import json

    ap = argparse.ArgumentParser(prog="mememo migrate-worktrees")
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ns = ap.parse_args(args)

    from .core.git_manager import GitManager
    from .core.storage_manager import StorageManager
    from .types.config import MemoConfig
    from .utils.hashing import hash_path

    cfg = MemoConfig.from_env()
    storage = StorageManager(base_dir=cfg.storage.base_dir)
    git = GitManager()

    async def _run() -> int:
        canonical = await git.canonical_repo_root(ns.repo_path)
        canonical_id = hash_path(canonical)
        rows = storage.conn.execute(
            "SELECT DISTINCT repo_id, repo_path FROM memories WHERE repo_path IS NOT NULL"
        ).fetchall()
        migrations: list[tuple[str, str]] = []
        for row in rows:
            if not row["repo_path"] or hash_path(row["repo_path"]) == canonical_id:
                continue
            try:
                other_canonical = await git.canonical_repo_root(row["repo_path"])
            except RuntimeError:
                continue
            if hash_path(other_canonical) == canonical_id:
                migrations.append((row["repo_id"], canonical_id))

        if not migrations:
            print("migrate-worktrees: no orphaned repo_ids found.")
            return 0

        total: dict[str, int] = {}
        for old, new in migrations:
            if ns.dry_run:
                print(f"would reassign {old[:12]} -> {new[:12]}")
                continue
            counts = storage.reassign_repo_id(old, new)
            print(f"reassigned {old[:12]} -> {new[:12]}: {json.dumps(counts)}")
            for t, n in counts.items():
                total[t] = total.get(t, 0) + n
        if not ns.dry_run:
            print(f"totals: {json.dumps(total)}")
        return 0

    return asyncio.run(_run())


def _cmd_merge_branch(args: list[str]) -> int:
    """CLI shim for the merge_branch MCP tool — called by the post-merge hook."""
    import argparse
    import asyncio

    ap = argparse.ArgumentParser(prog="mememo merge-branch")
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--source", required=True, dest="source_branch")
    ap.add_argument("--target", required=True, dest="target_branch")
    ap.add_argument("--merge-sha", default=None)
    ns = ap.parse_args(args)

    async def _run() -> int:
        from .server import initialize_mememo
        from .tools.merge_branch import MergeBranchParams, merge_branch

        await initialize_mememo()
        import mememo.server as srv

        resp = await merge_branch(
            MergeBranchParams(
                repo_path=ns.repo_path,
                source_branch=ns.source_branch,
                target_branch=ns.target_branch,
                merge_sha=ns.merge_sha,
            ),
            srv.memory_manager,
        )
        print(resp.message)
        return 0 if resp.success else 1

    return asyncio.run(_run())


def _cmd_sync_commits(args: list[str]) -> int:
    """CLI shim for the sync_commits MCP tool — called by the post-commit hook."""
    import argparse
    import asyncio

    ap = argparse.ArgumentParser(prog="mememo sync-commits")
    ap.add_argument("--repo-path", required=True)
    ap.add_argument(
        "--file-patterns",
        nargs="*",
        default=["**/*.py", "**/*.ts", "**/*.js", "**/*.go"],
    )
    ns = ap.parse_args(args)

    async def _run() -> int:
        from .server import initialize_mememo
        from .tools.schemas import SyncCommitsParams
        from .tools.sync_commits import sync_commits

        await initialize_mememo()
        import mememo.server as srv

        resp = await sync_commits(
            SyncCommitsParams(repo_path=ns.repo_path, file_patterns=ns.file_patterns),
            srv.memory_manager,
        )
        print(resp.message)
        return 0 if resp.success else 1

    return asyncio.run(_run())


_SUBCOMMANDS = {
    "install-git-hooks": _cmd_install_git_hooks,
    "serve": _cmd_serve,
    "migrate-worktrees": _cmd_migrate_worktrees,
    "merge-branch": _cmd_merge_branch,
    "sync-commits": _cmd_sync_commits,
}


# --- entry point ------------------------------------------------------------


def main() -> None:
    args = sys.argv[1:]

    # Fast-path hook subcommands: `<name> --hook`. argparse adds milliseconds
    # of import cost and Claude Code invokes these per turn.
    if len(args) >= 2 and args[1] == "--hook":
        hook_name = args[0]
        if hook_name in ("capture", "inject", "pre-tool"):
            # Try the sidecar in the running MCP server first (sub-100ms vs ~3s
            # cold). Falls through to the slow path on any daemon trouble.
            if os.environ.get("MEMEMO_NO_HOOK_CLIENT") != "1":
                try:
                    from .hookclient import DaemonUnavailableError
                    from .hookclient import run as _hook_run

                    sys.exit(_hook_run(hook_name))
                except DaemonUnavailableError:
                    pass  # fall through to in-process init
            if hook_name == "capture":
                from .cli import run_capture

                run_capture()
                return
            if hook_name == "inject":
                from .cli import run_inject

                run_inject()
                return
            if hook_name == "pre-tool":
                from .cli import run_pre_tool

                run_pre_tool()
                return

    if args and args[0] in _SUBCOMMANDS:
        sys.exit(_SUBCOMMANDS[args[0]](args[1:]))

    import argparse

    from .server import run

    epilog = (
        "subcommands:\n"
        + "\n".join(
            f"  {name:<22} {_subcommand_help(name)}"
            for name in (
                "serve",
                "install-git-hooks",
                "migrate-worktrees",
                "merge-branch",
                "sync-commits",
                "capture --hook",
                "inject --hook",
                "pre-tool --hook",
            )
        )
        + "\n\nRun `python -m mememo <subcommand> --help` for subcommand options."
    )

    parser = argparse.ArgumentParser(
        prog="python -m mememo",
        description=(
            f"mememo v{__version__} - Code-aware memory server.\n"
            "With no arguments, starts the MCP server on stdio."
        ),
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"mememo v{__version__}")
    parser.parse_args(args)
    print(f"Starting mememo v{__version__}...", file=sys.stderr)
    run()


def _subcommand_help(name: str) -> str:
    return {
        "serve": "Launch localhost web UI (requires [web] extra)",
        "install-git-hooks": "Install opt-in post-merge / post-commit / pre-tool hooks",
        "migrate-worktrees": "Re-key legacy per-worktree repo_ids onto the canonical one",
        "merge-branch": "Shim over merge_branch MCP tool (called by post-merge hook)",
        "sync-commits": "Shim over sync_commits MCP tool (called by post-commit hook)",
        "capture --hook": "Stop-hook fast path: auto-capture session transcript",
        "inject --hook": "UserPromptSubmit fast path: inject recall context",
        "pre-tool --hook": "PreToolUse fast path: emit related-memory block",
    }.get(name, "")


if __name__ == "__main__":
    main()
