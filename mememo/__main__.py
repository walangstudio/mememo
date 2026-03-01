"""
CLI entry point for mememo.

Usage:
    python -m mememo              # Run MCP server
    python -m mememo --version    # Show version
"""

import argparse
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("mememo")
except PackageNotFoundError:
    __version__ = "unknown"

from .server import run


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description=f"mememo v{__version__} - Code-aware memory server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"mememo v{__version__}",
    )

    parser.parse_args()

    print(f"Starting mememo v{__version__}...")
    run()


if __name__ == "__main__":
    main()
