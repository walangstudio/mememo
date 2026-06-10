"""Initialization is time-bounded so a stalled first-call init (model download,
git subprocess, leaked sibling server holding the store) surfaces a readable
error instead of hanging the MCP server forever."""

from __future__ import annotations

import asyncio
import os
import threading
import time

import pytest


@pytest.fixture
def srv(monkeypatch):
    import mememo.server as server

    monkeypatch.setattr(server, "memory_manager", None)
    monkeypatch.setattr(server, "_INIT_TIMEOUT_S", 30.0)
    return server


def test_runs_init_once(srv, monkeypatch):
    calls = []

    async def fake_init():
        calls.append(1)
        await asyncio.sleep(0.02)
        srv.memory_manager = object()

    monkeypatch.setattr(srv, "initialize_mememo", fake_init)
    asyncio.run(srv.ensure_initialized())
    assert srv.memory_manager is not None
    assert len(calls) == 1


def test_concurrent_callers_init_once(srv, monkeypatch):
    calls = []

    async def fake_init():
        calls.append(1)
        await asyncio.sleep(0.05)
        srv.memory_manager = object()

    monkeypatch.setattr(srv, "initialize_mememo", fake_init)

    async def _both():
        await asyncio.gather(srv.ensure_initialized(), srv.ensure_initialized())

    asyncio.run(_both())
    assert len(calls) == 1


def test_skips_when_already_initialized(srv, monkeypatch):
    sentinel = object()
    monkeypatch.setattr(srv, "memory_manager", sentinel)

    async def boom():
        raise AssertionError("init must not run when already initialized")

    monkeypatch.setattr(srv, "initialize_mememo", boom)
    asyncio.run(srv.ensure_initialized())
    assert srv.memory_manager is sentinel


def test_times_out_with_clear_error(srv, monkeypatch):
    monkeypatch.setattr(srv, "_INIT_TIMEOUT_S", 0.2)
    release = threading.Event()

    async def slow_init():
        # Block (never touching globals, so a lingering worker can't corrupt a
        # later test) until released or a hard cap.
        for _ in range(200):
            if release.is_set():
                return
            await asyncio.sleep(0.01)

    monkeypatch.setattr(srv, "initialize_mememo", slow_init)
    # A dedicated loop, not asyncio.run: run_until_complete returns the moment the
    # error propagates, whereas asyncio.run's shutdown would join the (correctly)
    # still-running worker thread and mask the prompt return.
    loop = asyncio.new_event_loop()
    try:
        t0 = time.monotonic()
        with pytest.raises(RuntimeError, match="exceeded"):
            loop.run_until_complete(srv.ensure_initialized())
        assert time.monotonic() - t0 < 1.5  # bounded, not the full block
    finally:
        release.set()
        time.sleep(0.05)  # let the worker thread exit
        loop.close()


def test_zero_timeout_disables_bound(srv, monkeypatch):
    monkeypatch.setattr(srv, "_INIT_TIMEOUT_S", 0.0)

    async def fake_init():
        await asyncio.sleep(0.1)
        srv.memory_manager = object()

    monkeypatch.setattr(srv, "initialize_mememo", fake_init)
    asyncio.run(srv.ensure_initialized())
    assert srv.memory_manager is not None


def test_hf_download_timeout_sets_defaults(monkeypatch):
    from mememo.embeddings.embedder import _bound_hf_download_timeout

    monkeypatch.delenv("HF_HUB_DOWNLOAD_TIMEOUT", raising=False)
    monkeypatch.delenv("HF_HUB_ETAG_TIMEOUT", raising=False)
    monkeypatch.delenv("MEMEMO_HF_TIMEOUT", raising=False)
    _bound_hf_download_timeout()
    assert os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] == "30"
    assert os.environ["HF_HUB_ETAG_TIMEOUT"] == "30"


def test_hf_download_timeout_respects_explicit(monkeypatch):
    from mememo.embeddings.embedder import _bound_hf_download_timeout

    monkeypatch.setenv("HF_HUB_DOWNLOAD_TIMEOUT", "5")
    monkeypatch.delenv("HF_HUB_ETAG_TIMEOUT", raising=False)
    monkeypatch.setenv("MEMEMO_HF_TIMEOUT", "99")
    _bound_hf_download_timeout()
    assert os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] == "5"  # explicit user value wins
    assert os.environ["HF_HUB_ETAG_TIMEOUT"] == "99"  # unset -> MEMEMO_HF_TIMEOUT
