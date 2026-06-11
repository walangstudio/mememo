"""Tests for the single-instance guard (mememo/single_instance.py).

The guard reaps older orphaned mememo servers on startup so reconnect orphans
can't pile up, while sparing same-connection peers (spawned within a short
window), and exposes ``live_sibling_servers`` for check_memory's diagnostics.
"""

from __future__ import annotations

import json
import os

import pytest

from mememo import single_instance as si


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Default to the home store so fake procs with no MEMEMO_STORAGE_DIR are
    # treated as same-store; tests that need a specific store set it in-body.
    monkeypatch.delenv("MEMEMO_STORAGE_DIR", raising=False)
    monkeypatch.delenv("MEMEMO_NO_REAP", raising=False)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "data"))
    return tmp_path


class _FakeProc:
    """Minimal psutil.Process stand-in for process_iter(attrs=...)."""

    def __init__(self, pid, cmdline, create_time, running=True, env=None):
        self.pid = pid
        self.info = {"pid": pid, "cmdline": cmdline, "create_time": create_time, "exe": "python"}
        self._running = running
        self._env = env if env is not None else {}
        self.terminated = False
        self.killed = False

    def is_running(self):
        return self._running

    def environ(self):
        return self._env

    def terminate(self):
        self.terminated = True
        self._running = False

    def wait(self, timeout=None):
        pass

    def kill(self):  # pragma: no cover - only if terminate didn't stop it
        self.killed = True
        self._running = False


class _NoSuchProcessError(Exception):
    pass


def _fake_psutil(procs):
    return type(
        "PS",
        (),
        {
            "process_iter": staticmethod(lambda attrs=None: iter(procs)),
            "NoSuchProcess": _NoSuchProcessError,
        },
    )


# --- cmdline classification -------------------------------------------------


def test_is_server_cmdline_matches_bare_module():
    assert si._is_server_cmdline(["C:\\py\\python.exe", "-m", "mememo"])
    assert si._is_server_cmdline(["/usr/bin/python", "-m", "mememo"])


def test_is_server_cmdline_matches_console_script():
    assert si._is_server_cmdline(["C:\\venv\\Scripts\\mememo.exe"])
    assert si._is_server_cmdline(["/venv/bin/mememo"])


def test_is_server_cmdline_rejects_hooks_and_subcommands():
    assert not si._is_server_cmdline(["python", "-m", "mememo", "session-start", "--hook"])
    assert not si._is_server_cmdline(["python", "-m", "mememo", "index", "."])
    assert not si._is_server_cmdline(["python", "-m", "mememo", "serve"])
    assert not si._is_server_cmdline(["mememo", "curate-skills"])
    assert not si._is_server_cmdline([])
    assert not si._is_server_cmdline(["python", "-m", "other"])


# --- reap_orphan_servers ----------------------------------------------------


def test_reap_kills_older_server(monkeypatch):
    orphan = _FakeProc(111, ["python", "-m", "mememo"], create_time=100.0)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([orphan]))
    n = si.reap_orphan_servers(own_create_time=200.0)  # 100s older than us
    assert n == 1 and orphan.terminated


def test_reap_spares_same_connection_peer(monkeypatch):
    """A server started ~30ms before us is a same-connect peer, not an orphan."""
    peer = _FakeProc(111, ["python", "-m", "mememo"], create_time=199.97)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([peer]))
    n = si.reap_orphan_servers(own_create_time=200.0)
    assert n == 0 and not peer.terminated


def test_reap_spares_younger_server(monkeypatch):
    younger = _FakeProc(111, ["python", "-m", "mememo"], create_time=500.0)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([younger]))
    assert si.reap_orphan_servers(own_create_time=200.0) == 0
    assert not younger.terminated


def test_reap_ignores_non_server_processes(monkeypatch):
    hook = _FakeProc(111, ["python", "-m", "mememo", "session-start", "--hook"], create_time=10.0)
    other = _FakeProc(222, ["python", "-m", "other"], create_time=10.0)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([hook, other]))
    assert si.reap_orphan_servers(own_create_time=200.0) == 0
    assert not hook.terminated and not other.terminated


def test_reap_excludes_self(monkeypatch):
    me = _FakeProc(os.getpid(), ["python", "-m", "mememo"], create_time=10.0)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([me]))
    assert si.reap_orphan_servers(own_create_time=200.0) == 0
    assert not me.terminated


def test_reap_noop_without_own_create_time(monkeypatch):
    orphan = _FakeProc(111, ["python", "-m", "mememo"], create_time=1.0)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([orphan]))
    monkeypatch.setattr(si, "_own_create_time", lambda: None)
    assert si.reap_orphan_servers(own_create_time=None) == 0
    assert not orphan.terminated


def test_reap_noop_without_psutil(monkeypatch):
    monkeypatch.setattr(si, "_psutil", lambda: None)
    assert si.reap_orphan_servers(own_create_time=200.0) == 0


def test_reap_spares_different_store_server(monkeypatch):
    """A bare server pointed at a different MEMEMO_STORAGE_DIR doesn't contend."""
    monkeypatch.delenv("MEMEMO_STORAGE_DIR", raising=False)  # we use the default store
    other = _FakeProc(
        111,
        ["python", "-m", "mememo"],
        create_time=10.0,
        env={"MEMEMO_STORAGE_DIR": "/some/other/store/data"},
    )
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([other]))
    assert si.reap_orphan_servers(own_create_time=200.0) == 0
    assert not other.terminated


def test_reap_kills_same_store_server(monkeypatch, tmp_path):
    store = str(tmp_path / "data")
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", store)
    same = _FakeProc(
        111,
        ["python", "-m", "mememo"],
        create_time=10.0,
        env={"MEMEMO_STORAGE_DIR": store},
    )
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([same]))
    assert si.reap_orphan_servers(own_create_time=200.0) == 1
    assert same.terminated


def test_reap_kills_when_store_unreadable(monkeypatch):
    """Unknown store (environ() raises) falls back to reaping — same-machine default."""
    monkeypatch.delenv("MEMEMO_STORAGE_DIR", raising=False)

    class _Blind(_FakeProc):
        def environ(self):
            raise RuntimeError("access denied")

    orphan = _Blind(111, ["python", "-m", "mememo"], create_time=10.0)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([orphan]))
    assert si.reap_orphan_servers(own_create_time=200.0) == 1
    assert orphan.terminated


def test_reap_survives_process_raising_mid_iteration(monkeypatch):
    """One process raising on attribute access must not abort the whole sweep."""

    class _Exploding(_FakeProc):
        @property
        def info(self):
            raise RuntimeError("vanished")

        @info.setter
        def info(self, v):
            self._info = v

    boom = _Exploding(111, ["python", "-m", "mememo"], create_time=10.0)
    good = _FakeProc(222, ["python", "-m", "mememo"], create_time=10.0)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([boom, good]))
    assert si.reap_orphan_servers(own_create_time=200.0) == 1
    assert good.terminated


# --- claim_singleton --------------------------------------------------------


def test_claim_singleton_writes_pidfile(store, monkeypatch):
    monkeypatch.setattr(si, "reap_orphan_servers", lambda ct: 0)
    si.claim_singleton(version="9.9.9")
    data = json.loads(si._pidfile_path().read_text(encoding="utf-8"))
    assert data["pid"] == os.getpid()
    assert data["version"] == "9.9.9"


def test_claim_singleton_reaps_by_default(store, monkeypatch):
    called = []
    monkeypatch.setattr(si, "reap_orphan_servers", lambda ct: called.append(ct) or 0)
    si.claim_singleton()
    assert called  # reap was invoked


def test_claim_singleton_skips_reap_when_disabled(store, monkeypatch):
    monkeypatch.setenv("MEMEMO_NO_REAP", "1")
    monkeypatch.setattr(si, "reap_orphan_servers", lambda ct: pytest.fail("reap must be skipped"))
    si.claim_singleton()
    assert json.loads(si._pidfile_path().read_text())["pid"] == os.getpid()


# --- live_sibling_servers ---------------------------------------------------


def test_live_sibling_servers_excludes_self_and_non_servers(monkeypatch):
    me = os.getpid()
    procs = [
        _FakeProc(me, ["python", "-m", "mememo"], create_time=1.0),
        _FakeProc(111, ["python", "-m", "mememo"], create_time=2.0),
        _FakeProc(222, ["python", "-m", "mememo", "index", "."], create_time=3.0),
    ]
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil(procs))
    pids = [s["pid"] for s in si.live_sibling_servers()]
    assert pids == [111]  # self excluded, the `index` subcommand excluded


def test_live_sibling_servers_empty_without_psutil(monkeypatch):
    monkeypatch.setattr(si, "_psutil", lambda: None)
    assert si.live_sibling_servers() == []
