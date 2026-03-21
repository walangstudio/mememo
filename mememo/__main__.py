"""
CLI entry point for mememo.

Usage:
    python -m mememo                   # Run MCP server
    python -m mememo --version         # Show version
    python -m mememo capture --hook    # Stop hook: auto-capture from transcript
    python -m mememo inject --hook     # UserPromptSubmit hook: inject context
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
