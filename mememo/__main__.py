"""
CLI entry point for mememo.

Usage:
    python -m mememo                   # Run MCP server
    python -m mememo --version         # Show version
    python -m mememo capture --hook    # Stop hook: auto-capture from transcript
    python -m mememo inject --hook     # UserPromptSubmit hook: inject context
    python -m mememo install-git-hooks --repo-path <path> [--force]
                                       # Install opt-in post-merge / post-commit
                                       # git hooks into <path>/.git/hooks/ (FR-033)
"""

import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("mememo")
except PackageNotFoundError:
    __version__ = "unknown"


def main():
    args = sys.argv[1:]

    # Hook subcommands — bypass argparse for speed
    if len(args) >= 2 and args[1] == "--hook":
        if args[0] == "capture":
            from .cli import run_capture

            run_capture()
            return
        if args[0] == "inject":
            from .cli import run_inject

            run_inject()
            return
        if args[0] == "pre-tool":
            # v0.6 PreToolUse hook (T032 / FR-028).
            from .cli import run_pre_tool

            run_pre_tool()
            return

    # v0.4 hook installer subcommand (FR-033, T013/T014).
    if args and args[0] == "install-git-hooks":
        import argparse as _argparse

        ap = _argparse.ArgumentParser(prog="mememo install-git-hooks")
        ap.add_argument("--repo-path", required=True)
        ap.add_argument("--force", action="store_true", help="overwrite existing hooks")
        ap.add_argument(
            "--with-pretool",
            action="store_true",
            help="also register PreToolUse hook in .claude/settings.json (T032)",
        )
        ns = ap.parse_args(args[1:])

        from .hooks.installer import install_git_hooks, register_claude_pretool_hook

        result = install_git_hooks(ns.repo_path, force=ns.force)
        print(result.report())
        ok = result.ok
        if ns.with_pretool:
            pre = register_claude_pretool_hook(ns.repo_path, force=ns.force)
            print(f"pre-tool: {pre.get('status')} ({pre.get('settings_path')})")
            if pre.get("status") == "error":
                ok = False
        sys.exit(0 if ok else 1)

    # v0.6 worktree migration subcommand (FR-025, T030).
    if args and args[0] == "migrate-worktrees":
        import argparse as _argparse
        import asyncio as _asyncio
        import json as _json

        ap = _argparse.ArgumentParser(prog="mememo migrate-worktrees")
        ap.add_argument("--repo-path", required=True,
                        help="working directory inside the target repo")
        ap.add_argument("--dry-run", action="store_true",
                        help="report what would change without modifying the store")
        ns = ap.parse_args(args[1:])

        from .core.git_manager import GitManager
        from .core.storage_manager import StorageManager
        from .types.config import MemoConfig
        from .utils.hashing import hash_path

        cfg = MemoConfig.from_env()
        storage = StorageManager(base_dir=cfg.storage.path)
        git = GitManager()

        async def _do() -> int:
            canonical = await git.canonical_repo_root(ns.repo_path)
            canonical_id = hash_path(canonical)
            # Find every repo_id whose stored path differs from canonical.
            rows = storage.conn.execute(
                "SELECT DISTINCT repo_id, repo_path FROM memories WHERE repo_path IS NOT NULL"
            ).fetchall()
            migrations: list[tuple[str, str]] = []
            for row in rows:
                if not row["repo_path"]:
                    continue
                if hash_path(row["repo_path"]) == canonical_id:
                    continue
                # Resolve this repo_path's canonical root.
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
                print(f"reassigned {old[:12]} -> {new[:12]}: {_json.dumps(counts)}")
                for t, n in counts.items():
                    total[t] = total.get(t, 0) + n
            if not ns.dry_run:
                print(f"totals: {_json.dumps(total)}")
            return 0

        sys.exit(_asyncio.run(_do()))

    import argparse

    from .server import run

    parser = argparse.ArgumentParser(
        description=f"mememo v{__version__} - Code-aware memory server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"mememo v{__version__}",
    )
    parser.parse_args(args)

    print(f"Starting mememo v{__version__}...")
    run()


if __name__ == "__main__":
    main()
