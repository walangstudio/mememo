"""Cold-boot smoke test: server module imports and MCP object constructs without hanging.

mememo has had recurring cold-boot disconnect/hang bugs (v0.41/v0.42 fixes).
This asserts the fast path — module import + FastMCP object construction — is
safe and fully offline, i.e. no model download or blocking init on import.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch):
    """Block any accidental HuggingFace model download."""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")


def test_server_import_constructs_mcp_without_init():
    """Importing mememo.server builds the FastMCP object but leaves memory_manager None.

    initialize_mememo() is the only path that loads the embedding model and
    touches the network; it must NOT be called at import time.
    """
    from fastmcp import FastMCP

    import mememo.server as server

    assert isinstance(server.mcp, FastMCP), "FastMCP server object must exist after import"
    assert server.memory_manager is None, "initialize_mememo() must not run on import"


def test_server_mcp_name():
    """FastMCP server carries the expected application name."""
    import mememo.server as server

    assert server.mcp.name == "mememo"
