"""CLI entry point for mememo.

Usage:
    python -m mememo                        # Run MCP server
    python -m mememo --version              # Show version
    python -m mememo capture --hook         # Stop hook: auto-capture
    python -m mememo distill --hook         # Stop hook (sync): distill a reusable skill
    python -m mememo inject --hook          # UserPromptSubmit: inject context
    python -m mememo pre-tool --hook        # PreToolUse: related-memory block
    python -m mememo session-start --hook   # SessionStart: recall memories
    python -m mememo install-git-hooks --repo-path <p> [--force] [--with-pretool]
    python -m mememo migrate-worktrees --repo-path <p> [--dry-run]
    python -m mememo merge-branch --repo-path <p> --source <b> --target <b> [--merge-sha <sha>]
    python -m mememo sync-commits --repo-path <p>
    python -m mememo import-md <dir> [--repo <path>] [--dry-run]
    python -m mememo reindex-identity [--dry-run]
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


def _cmd_import_md(args: list[str]) -> int:
    from .cli import cmd_import_md

    return cmd_import_md(args)


def _cmd_index(args: list[str]) -> int:
    """Index a repository into mememo from the CLI (the explicit first-index)."""
    import argparse
    import asyncio

    ap = argparse.ArgumentParser(prog="mememo index")
    ap.add_argument("path", nargs="?", default=".", help="Repo path (default: cwd)")
    ap.add_argument("--full", action="store_true", help="Force a full (non-incremental) re-index")
    ap.add_argument(
        "--patterns", nargs="*", default=None, help="Glob patterns (default: code exts)"
    )
    ap.add_argument("--max-files", type=int, default=1000)
    ap.add_argument("--watch", action="store_true", help="Re-index on an interval until Ctrl-C")
    ap.add_argument("--interval", type=float, default=60.0, help="--watch poll seconds")
    ap.add_argument("--quiet", action="store_true")
    # Set by the auto-index spawner: a lock file this run owns and must release
    # on failure so a crashed child doesn't suppress retries for the whole TTL.
    ap.add_argument("--autoindex-lock", default=None, help=argparse.SUPPRESS)
    ns = ap.parse_args(args)

    async def _build() -> tuple:
        # Build the memory manager via the shared factory (same wiring the server
        # uses) rather than initialize_mememo: a one-shot CLI index doesn't need
        # the LLM adapter / skill store, and the background identity-migration
        # thread initialize_mememo spawns can trip a shared huggingface_hub httpx
        # client ("client has been closed") mid-index.
        from .core.bootstrap import build_memory_manager
        from .types.config import MemoConfig

        cfg = MemoConfig.from_env()
        mm, _repo_id, _branch = await build_memory_manager(cfg, repo_path=ns.path)
        return cfg, mm

    async def _index_once(cfg, mm, first: bool) -> bool:
        from .tools.index_repository import index_repository
        from .tools.schemas import IndexRepositoryParams

        kw: dict = {
            "repo_path": ns.path,
            "incremental": not (ns.full and first),
            "max_files": ns.max_files,
        }
        if ns.patterns:
            kw["file_patterns"] = ns.patterns
        resp = await index_repository(
            IndexRepositoryParams(**kw), mm, ignored_dirs=cfg.indexing.ignored_dirs
        )
        if not ns.quiet:
            print(resp.message)
        return resp.success

    async def _run() -> int:
        cfg, mm = await _build()
        ok = await _index_once(cfg, mm, first=True)
        if not ns.watch:
            return 0 if ok else 1
        sys.stderr.write(f"watching {ns.path} every {ns.interval:.0f}s — Ctrl-C to stop\n")
        try:
            while True:
                await asyncio.sleep(ns.interval)
                if not await _index_once(cfg, mm, first=False):
                    # index_repository swallows errors and returns success=False;
                    # surface it so a repo that fails every tick isn't silent.
                    sys.stderr.write(f"mememo index: watch round failed for {ns.path}\n")
        except (KeyboardInterrupt, asyncio.CancelledError):
            return 0

    # When spawned as a background auto-index child, the parent passed the lock
    # path. Release it on failure so the next session retries instead of waiting
    # out the TTL; leave it on success so the TTL rate-limit works.
    lock_path = ns.autoindex_lock
    try:
        rc = asyncio.run(_run())
    except KeyboardInterrupt:
        rc = 0
    except Exception as exc:
        sys.stderr.write(f"mememo index failed: {exc}\n")
        rc = 1
    if lock_path and rc != 0:
        try:
            os.unlink(lock_path)
        except OSError:
            pass
    return rc


def _cmd_reindex_identity(args: list[str]) -> int:
    from .cli import cmd_reindex_identity

    return cmd_reindex_identity(args)


def _open_in_browser(path: str) -> None:
    import webbrowser
    from pathlib import Path

    webbrowser.open(Path(path).resolve().as_uri())


def _cmd_render(args: list[str]) -> int:
    """Convert a Mermaid (.mmd) file (or stdin) into a double-clickable .html."""
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(prog="mememo render")
    ap.add_argument("input", help="Mermaid .mmd file, or '-' for stdin")
    ap.add_argument("--out", default=None, help="Output .html (default: alongside input)")
    ap.add_argument("--title", default="mememo diagram")
    ap.add_argument("--no-open", action="store_true", help="Don't open the browser")
    ns = ap.parse_args(args)

    if ns.input == "-":
        mermaid = sys.stdin.read()
        out = ns.out or "mememo-diagram.html"
    else:
        src = Path(ns.input)
        if not src.is_file():
            sys.stderr.write(f"render: no such file: {ns.input}\n")
            return 1
        mermaid = src.read_text(encoding="utf-8")
        out = ns.out or str(src.with_suffix(".html"))

    from .diagram_html import write_html

    write_html(mermaid, out, title=ns.title)
    print(f"wrote {out}")
    if not ns.no_open:
        _open_in_browser(out)
    return 0


def _cmd_diagram(args: list[str]) -> int:
    """Generate a deterministic diagram from the index and open it in the browser."""
    import argparse
    import asyncio
    from pathlib import Path

    ap = argparse.ArgumentParser(prog="mememo diagram")
    ap.add_argument("type", choices=["class", "call", "module", "overview"], help="Diagram type")
    ap.add_argument("--scope", default=None, help="class: file/class; call: function/memory_id")
    ap.add_argument(
        "--repo", default=None, help="Repo path to pick repo_id/branch (default: busiest)"
    )
    ap.add_argument("--out", default=None, help="Output .html path")
    ap.add_argument("--no-open", action="store_true")
    ns = ap.parse_args(args)

    from .core.storage_manager import StorageManager
    from .diagram_html import write_html
    from .diagrams import call_graph, class_diagram, module_dependency, overview_diagram
    from .types.config import MemoConfig

    storage = StorageManager(base_dir=MemoConfig.from_env().storage.base_dir)
    conn = storage.conn

    repo_id, branch = _resolve_cli_repo(conn, ns.repo)
    if repo_id is None:
        sys.stderr.write("No indexed repo found. Index one first (index_repository).\n")
        return 1

    if ns.type == "class":
        mermaid = class_diagram(conn, repo_id, branch, scope=ns.scope, base_dir=storage.base_dir)
    elif ns.type == "module":
        mermaid = module_dependency(conn, repo_id, branch)
    elif ns.type == "overview":
        mermaid = overview_diagram(conn, repo_id, branch)
    else:  # call
        from .tools.generate_diagram import resolve_call_root

        root = asyncio.run(resolve_call_root(conn, repo_id, branch, ns.scope))
        if root is None:
            sys.stderr.write(
                f"Could not resolve a call-graph root from scope={ns.scope!r}. "
                "Pass a function name that exists in the index.\n"
            )
            return 1
        mermaid = call_graph(conn, root, depth=3)

    out = ns.out or f"mememo-{ns.type}.html"
    write_html(mermaid, out, title=f"mememo {ns.type} diagram")
    print(f"wrote {Path(out).resolve()}")
    if not ns.no_open:
        _open_in_browser(out)
    return 0


def _resolve_cli_repo(conn, repo_path: str | None) -> tuple[str | None, str | None]:
    """Pick (repo_id, branch): detect from repo_path, else the busiest indexed repo."""
    if repo_path:
        import asyncio

        from .core.git_manager import GitManager

        try:
            ctx = asyncio.run(GitManager().detect_context(repo_path))
            return ctx.repo.id, ctx.branch.name
        except Exception as e:
            sys.stderr.write(
                f"warning: could not detect repo from {repo_path!r} ({e}); "
                "falling back to the busiest indexed repo.\n"
            )
    row = conn.execute(
        "SELECT repo_id, branch_name FROM memories "
        "GROUP BY repo_id, branch_name ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()
    return (row["repo_id"], row["branch_name"]) if row else (None, None)


def _cmd_curate_skills(args: list[str]) -> int:
    """Consolidate the distilled-skill library headlessly (for cron / `/schedule`).

    The deterministic prunes (exact dupes, never-used stale skills) run without a
    model; near-duplicate clusters are printed as a merge prompt for the next host
    session to act on (passthrough). Dry by default; pass --apply to delete.
    """
    import argparse
    import asyncio
    import os

    from .context.skill_curator import DEFAULT_DUP_THRESHOLD

    ap = argparse.ArgumentParser(prog="mememo curate-skills")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Delete exact-duplicate skills (and never-used stale skills with "
        "--stale-days). Default is a dry preview.",
    )
    ap.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_DUP_THRESHOLD,
        help=f"Near-duplicate cosine threshold (default {DEFAULT_DUP_THRESHOLD}).",
    )
    ap.add_argument(
        "--stale-days",
        type=int,
        default=0,
        help="Prune skills never injected AND not modified in N days (0 = off).",
    )
    ap.add_argument("--json", action="store_true", help="Emit the raw JSON response.")
    # Set by the auto-curate spawner: a lock this run owns and must release on
    # failure so a crashed background child doesn't suppress retries for the TTL.
    ap.add_argument("--lock", default=None, help=argparse.SUPPRESS)
    ns = ap.parse_args(args)

    from .server import ensure_initialized
    from .tools.curate_skills import curate_skills as curate_impl
    from .tools.schemas import CurateSkillsParams

    async def _run():
        await ensure_initialized()
        import mememo.server as srv

        return await curate_impl(
            CurateSkillsParams(
                apply=ns.apply, threshold=ns.threshold, stale_unused_days=ns.stale_days
            ),
            srv.skill_store,
            srv.memory_manager,
        )

    def _release_lock():
        if ns.lock:
            try:
                os.unlink(ns.lock)
            except OSError:
                pass

    try:
        res = asyncio.run(_run())
    except Exception as exc:
        sys.stderr.write(f"mememo curate-skills failed: {exc}\n")
        _release_lock()  # crashed child: free the slot so the next session retries
        return 1
    if not res.success:
        _release_lock()
        return 1

    if ns.json:
        print(res.model_dump_json(indent=2))
        return 0

    print(res.message)
    if res.removed_exact:
        print(f"  removed (exact dupes): {', '.join(res.removed_exact)}")
    if res.removed_unused:
        print(f"  removed (never-used):  {', '.join(res.removed_unused)}")
    if res.unused_candidates:
        print(
            f"  prunable (never-used): {', '.join(res.unused_candidates)} "
            "[re-run with --apply to delete]"
        )
    for i, cluster in enumerate(res.clusters, 1):
        print(f"  near-dup cluster {i}: " + ", ".join(c["name"] for c in cluster))
    if res.passthrough and res.passthrough_prompt:
        print("\n--- merge prompt (hand to a host model / next session) ---")
        print(res.passthrough_prompt)
    return 0  # success keeps the lock so the TTL rate-limit holds


def _cmd_export_skills(args: list[str]) -> int:
    """Export distilled skills to a directory of agentskills.io ``<name>/SKILL.md`` files."""
    import argparse
    from pathlib import Path

    ap = argparse.ArgumentParser(prog="mememo export-skills")
    ap.add_argument("out_dir", help="Directory to write <name>/SKILL.md trees into")
    ap.add_argument("--force", action="store_true", help="Overwrite existing SKILL.md files")
    ns = ap.parse_args(args)

    from .context.skill_portability import skill_to_skillmd
    from .context.skill_store import SkillStore
    from .types.config import MemoConfig

    ss = SkillStore(base_dir=MemoConfig.from_env().storage.base_dir)
    skills = ss.list_skills()
    if not skills:
        print("No skills to export.")
        return 0

    out = Path(ns.out_dir)
    written = 0
    seen: set[str] = set()
    for skill in skills:
        name, text = skill_to_skillmd(skill)
        if name in seen:
            # Two distinct skill names collapse to the same SKILL.md dir name
            # (e.g. "git_ops" and "git-ops" -> "git-ops"). Don't silently clobber.
            print(f"skip (name collision -> {name}): {skill.name}")
            continue
        seen.add(name)
        path = out / name / "SKILL.md"
        if path.exists() and not ns.force:
            print(f"skip (exists): {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written += 1
    print(f"Exported {written}/{len(skills)} skill(s) to {out}")
    return 0


def _cmd_import_skills(args: list[str]) -> int:
    """Import agentskills.io ``SKILL.md`` skill files from a directory tree."""
    import argparse
    import asyncio
    from pathlib import Path

    ap = argparse.ArgumentParser(prog="mememo import-skills")
    ap.add_argument("in_dir", help="Directory tree containing <name>/SKILL.md files")
    ap.add_argument("--dry-run", action="store_true", help="List what would import without writing")
    ap.add_argument(
        "--force",
        action="store_true",
        help="Overwrite skills that already exist (default: skip them)",
    )
    ns = ap.parse_args(args)

    src = Path(ns.in_dir)
    if not src.is_dir():
        sys.stderr.write(f"import-skills: not a directory: {src}\n")
        return 1

    from .context.skill_portability import parse_skillmd

    # Only canonical SKILL.md files — a blanket *.md sweep would slurp READMEs / docs
    # that merely happen to carry a `name:` frontmatter key.
    files = sorted(src.rglob("SKILL.md"))
    parsed = []
    for f in files:
        try:
            data = parse_skillmd(f.read_text(encoding="utf-8"))
        except OSError:
            continue
        if data:
            parsed.append(data)
    if not parsed:
        print("No importable SKILL.md skills found.")
        return 0

    if ns.dry_run:
        for d in parsed:
            print(f"would import: {d['name']} (intent={d['intent']}, priority={d['priority']})")
        print(f"{len(parsed)} skill(s) would be imported.")
        return 0

    from .server import ensure_initialized
    from .tools.manage_skill import manage_skill as manage_impl
    from .tools.schemas import ManageSkillParams

    async def _run():
        await ensure_initialized()
        import mememo.server as srv

        n = 0
        skipped = 0
        for d in parsed:
            if not ns.force and srv.skill_store.get_skill(d["name"]) is not None:
                print(f"skip (exists): {d['name']}  [--force to overwrite]")
                skipped += 1
                continue
            resp = await manage_impl(
                ManageSkillParams(
                    action="create",
                    name=d["name"],
                    intent=d["intent"],
                    prompt=d["prompt"],
                    priority=d["priority"],
                    tags=d["tags"],
                ),
                srv.skill_store,
                srv.memory_manager,
            )
            if resp.success:
                n += 1
            else:
                sys.stderr.write(f"import-skills: {d['name']}: {resp.message}\n")
        return n, skipped

    n, skipped = asyncio.run(_run())
    tail = f" ({skipped} skipped)" if skipped else ""
    print(f"Imported {n}/{len(parsed)} skill(s).{tail}")
    return 0


_SUBCOMMANDS = {
    "install-git-hooks": _cmd_install_git_hooks,
    "serve": _cmd_serve,
    "index": _cmd_index,
    "diagram": _cmd_diagram,
    "render": _cmd_render,
    "curate-skills": _cmd_curate_skills,
    "export-skills": _cmd_export_skills,
    "import-skills": _cmd_import_skills,
    "migrate-worktrees": _cmd_migrate_worktrees,
    "merge-branch": _cmd_merge_branch,
    "sync-commits": _cmd_sync_commits,
    "import-md": _cmd_import_md,
    "reindex-identity": _cmd_reindex_identity,
}


# --- entry point ------------------------------------------------------------


def main() -> None:
    args = sys.argv[1:]

    # Fast-path hook subcommands: `<name> --hook`. argparse adds milliseconds
    # of import cost and Claude Code invokes these per turn.
    if len(args) >= 2 and args[1] == "--hook":
        hook_name = args[0]
        if hook_name == "distill":
            # Sync Stop hook: cheap (config + transcript scan only), no daemon —
            # decision:block only works from a synchronous hook.
            from .cli import run_distill

            run_distill()
            return
        if hook_name in ("capture", "inject", "pre-tool", "session-start"):
            # Try the sidecar in the running MCP server first (sub-100ms vs ~3s
            # cold). Falls through to the slow path on any daemon trouble.
            #
            # `inject` is read-only: on a daemon timeout we re-read the prompt and
            # recompute in-process. But hookclient.run drains stdin before it can
            # raise, so capture the payload up front and feed BOTH paths. The
            # side-effecting hooks (capture/session-start) deliberately do NOT
            # preserve stdin — their daemon handler keeps running after we
            # disconnect and commits, so a fed fallback would double-write.
            hook_stdin = sys.stdin.read() if hook_name == "inject" else None
            # Only inject (UserPromptSubmit, every prompt, watchdog-bounded) gets the
            # short 6s daemon budget. capture/session-start/pre-tool keep the original
            # 30s wait so this change stays scoped to the inject hang and does not push
            # those (still un-watchdogged) hooks onto their cold fallback sooner.
            daemon_timeout = None if hook_name == "inject" else 30.0
            if os.environ.get("MEMEMO_NO_HOOK_CLIENT") != "1":
                try:
                    from .hookclient import DaemonUnavailableError
                    from .hookclient import run as _hook_run

                    sys.exit(_hook_run(hook_name, stdin_text=hook_stdin, timeout=daemon_timeout))
                except DaemonUnavailableError:
                    pass  # fall through to in-process init
            if hook_name == "inject":
                import io

                sys.stdin = io.StringIO(hook_stdin or "")
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
            if hook_name == "session-start":
                from .cli import run_session_start

                run_session_start()
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
                "index",
                "diagram",
                "render",
                "curate-skills",
                "export-skills",
                "import-skills",
                "install-git-hooks",
                "migrate-worktrees",
                "merge-branch",
                "sync-commits",
                "import-md",
                "reindex-identity",
                "capture --hook",
                "distill --hook",
                "inject --hook",
                "pre-tool --hook",
                "session-start --hook",
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
        "index": "Index a repo into mememo (the explicit first-index); --watch to keep it fresh",
        "diagram": "Generate a class/call/module diagram and open it in the browser",
        "render": "Convert a Mermaid .mmd file into a double-clickable .html",
        "curate-skills": "Consolidate distilled skills (dedup + prune never-used); cron-friendly",
        "export-skills": "Export skills to agentskills.io <name>/SKILL.md files (portable)",
        "import-skills": "Import agentskills.io SKILL.md files into the skill store",
        "install-git-hooks": "Install opt-in post-merge / post-commit / pre-tool hooks",
        "migrate-worktrees": "Re-key legacy per-worktree repo_ids onto the canonical one",
        "merge-branch": "Shim over merge_branch MCP tool (called by post-merge hook)",
        "sync-commits": "Shim over sync_commits MCP tool (called by post-commit hook)",
        "import-md": "Import .md files from a directory as memories",
        "reindex-identity": "Re-derive repo_ids from git remote and move FAISS dirs",
        "capture --hook": "Stop-hook fast path: auto-capture session transcript",
        "distill --hook": "Stop-hook (sync): distill a reusable skill on complex sessions",
        "inject --hook": "UserPromptSubmit fast path: inject recall context",
        "pre-tool --hook": "PreToolUse fast path: emit related-memory block",
        "session-start --hook": "SessionStart fast path: recall memories at session open",
    }.get(name, "")


if __name__ == "__main__":
    main()
