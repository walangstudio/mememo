"""
CLI entry point for mememo.

Usage:
    python -m mememo              # Run MCP server
    python -m mememo --version    # Show version
"""

import sys
import argparse
from .server import run


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="mememo v0.1.0 - Code-aware memory server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--version",
        action="version",
        version="mememo v0.1.0",
    )

    args = parser.parse_args()

    # Run MCP server
    print("Starting mememo v0.1.0...")
    run()


if __name__ == "__main__":
    main()
