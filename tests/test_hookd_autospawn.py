"""Tests for the standalone hookd autospawn path (v0.51.0).

Covers the spawn throttle in ``__main__._spawn_hookd_once``, the inject/pre-tool
fast exit when a spawn is in flight, the hookd idle stamp the standalone daemon
uses to exit, and the pinned model cache dir.
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import pytest

import mememo.__main__ as main_mod
from mememo import hookclient, hookd


@pytest.fixture()
def store(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("MEMEMO_HOOKD_AUTOSPAWN", raising=False)
    (tmp_path / "data").mkdir()
    return tmp_path


class _PopenRecorder:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return object()


def test_spawn_creates_lock_and_launches_detached(store, monkeypatch):
    rec = _PopenRecorder()
    monkeypatch.setattr("subprocess.Popen", rec)
    assert main_mod._spawn_hookd_once() is True
    assert len(rec.calls) == 1
    argv, kwargs = rec.calls[0]
    assert argv[-1] == "hookd"
    assert (store / ".hookd.spawn.lock").exists()


def test_fresh_lock_throttles_second_spawn(store, monkeypatch):
    rec = _PopenRecorder()
    monkeypatch.setattr("subprocess.Popen", rec)
    assert main_mod._spawn_hookd_once() is True
    assert main_mod._spawn_hookd_once() is True  # in flight, but no second Popen
    assert len(rec.calls) == 1


def test_stale_lock_is_replaced(store, monkeypatch):
    rec = _PopenRecorder()
    monkeypatch.setattr("subprocess.Popen", rec)
    lock = store / ".hookd.spawn.lock"
    lock.touch()
    import os

    old = time.time() - 300
    os.utime(lock, (old, old))
    assert main_mod._spawn_hookd_once() is True
    assert len(rec.calls) == 1


def test_live_daemon_short_circuits_spawn(store, monkeypatch):
    rec = _PopenRecorder()
    monkeypatch.setattr("subprocess.Popen", rec)
    monkeypatch.setattr(hookclient, "_load_discovery", lambda: {"pid": 1, "port": 1, "token": "t"})
    assert main_mod._spawn_hookd_once() is False
    assert rec.calls == []


def test_autospawn_env_kill_switch(store, monkeypatch):
    monkeypatch.setenv("MEMEMO_HOOKD_AUTOSPAWN", "0")
    rec = _PopenRecorder()
    monkeypatch.setattr("subprocess.Popen", rec)
    assert main_mod._spawn_hookd_once() is False
    assert rec.calls == []


def test_inject_fast_exit_when_spawn_in_flight(store, monkeypatch, capsys):
    """No daemon + spawn in flight -> inject answers empty immediately, never
    running the doomed cold init."""
    monkeypatch.delenv("MEMEMO_NO_HOOK_CLIENT", raising=False)
    monkeypatch.setattr(main_mod, "_spawn_hookd_once", lambda: True)
    monkeypatch.setattr(sys, "argv", ["mememo", "inject", "--hook"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"user_prompt": "hi"})))
    main_mod.main()
    assert json.loads(capsys.readouterr().out) == {"continue": True}


def test_idle_stamp_resets_on_request(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(hookd, "_last_request", time.monotonic() - 100)
    assert hookd.seconds_since_last_request() >= 100

    async def _echo(stdin_text: str) -> None:
        print("ok")

    disc = tmp_path / ".daemon.json"
    port, token, shutdown = hookd.start(
        factories={"echo": _echo}, discovery_path=disc, version="test"
    )
    try:
        rc = hookclient.run("echo", stdin_text="x")
        assert rc == 0
        assert hookd.seconds_since_last_request() < 50
    finally:
        shutdown()


def test_model_cache_dir_env_override_and_never_raises(tmp_path, monkeypatch):
    from mememo.embeddings.embedder import _model_cache_dir

    monkeypatch.setenv("MEMEMO_MODEL_CACHE_DIR", str(tmp_path / "models"))
    assert _model_cache_dir() == str(tmp_path / "models")
    assert (tmp_path / "models").is_dir()

    # Un-creatable dir degrades to None (shared-HF-cache-only), never raises.
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    monkeypatch.setenv("MEMEMO_MODEL_CACHE_DIR", str(blocker / "models"))
    assert _model_cache_dir() is None


def test_claim_standalone_single_instance(store):
    assert main_mod._claim_standalone() is True
    # Live claimant (this process) blocks a second claim.
    assert main_mod._claim_standalone() is False
    # A dead claimant's file is reclaimed.
    main_mod._standalone_claim_path().write_text("999999999")
    assert main_mod._claim_standalone() is True
