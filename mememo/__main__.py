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

    # v0.4 hook installer subcommand (FR-033, T013/T014).
    if args and args[0] == "install-git-hooks":
        import argparse as _argparse

        ap = _argparse.ArgumentParser(prog="mememo install-git-hooks")
        ap.add_argument("--repo-path", required=True)
        ap.add_argument("--force", action="store_true", help="overwrite existing hooks")
        ns = ap.parse_args(args[1:])

        from .hooks.installer import install_git_hooks

        result = install_git_hooks(ns.repo_path, force=ns.force)
        print(result.report())
        sys.exit(0 if result.ok else 1)

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
