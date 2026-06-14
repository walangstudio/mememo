"""Tests for the single-instance guard (mememo/single_instance.py).

The guard reaps older orphaned mememo servers from THIS Claude session on startup
so reconnect orphans can't pile up, while sparing same-connection peers (spawned
within a short window), our own launcher, and — above all — a *different*
concurrent Claude session's live server (same store, different controlling
client). ``live_sibling_servers`` exposes the same-session contenders for
check_memory's diagnostics.
"""

from __future__ import annotations

import json
import os

import pytest

from mememo import single_instance as si

# A stand-in controlling client (the claude/node process). Server processes in
# these tests hang off it so they resolve to the same session unless stated.
CLIENT = 900
OTHER_CLIENT = 901


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
    """Minimal psutil.Process stand-in for process_iter(attrs=...) plus the
    parent()/name() walk the guard uses to find the controlling client."""

    def __init__(self, pid, cmdline, create_time, running=True, env=None, ppid=None, name="python"):
        self.pid = pid
        self.info = {"pid": pid, "cmdline": cmdline, "create_time": create_time, "exe": "python"}
        self._running = running
        self._env = env if env is not None else {}
        self._ppid = ppid
        self._name = name
        self._registry: dict | None = None  # wired by _fake_psutil
        self.terminated = False
        self.killed = False

    def is_running(self):
        return self._running

    def environ(self):
        return self._env

    def name(self):
        return self._name

    def parent(self):
        if self._ppid is None or self._registry is None:
            return None
        return self._registry.get(self._ppid)

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
    registry = {p.pid: p for p in procs}
    for p in procs:
        if hasattr(p, "_registry"):
            p._registry = registry

    def _process(pid):
        if pid in registry:
            return registry[pid]
        raise _NoSuchProcessError(pid)

    return type(
        "PS",
        (),
        {
            "process_iter": staticmethod(lambda attrs=None: iter(procs)),
            "Process": staticmethod(_process),
            "NoSuchProcess": _NoSuchProcessError,
        },
    )


def _client(pid=CLIENT):
    """A non-Python controlling-client process (the reap walk stops here)."""
    return _FakeProc(pid, ["claude.exe"], create_time=1.0, name="claude.exe")


def _server(pid, create_time, client=CLIENT, **kw):
    """A bare ``-m mememo`` server hanging off ``client``."""
    return _FakeProc(pid, ["python", "-m", "mememo"], create_time, ppid=client, **kw)


# --- cmdline classification -------------------------------------------------


def test_is_server_cmdline_matches_bare_module():
    assert si._is_server_cmdline(["C:\\py\\python.exe", "-m", "mememo"])
    assert si._is_server_cmdline(["/usr/bin/python", "-m", "mememo"])


def test_is_server_cmdline_matches_console_script():
    # os.path.join keeps the path separator platform-correct (the real psutil
    # cmdline uses the running platform's separator, and basename splits on it).
    assert si._is_server_cmdline([os.path.join("venv", "Scripts", "mememo.exe")])
    assert si._is_server_cmdline([os.path.join("venv", "bin", "mememo")])
    assert si._is_server_cmdline(["mememo"])
    assert si._is_server_cmdline(["mememo.exe"])


def test_is_server_cmdline_rejects_hooks_and_subcommands():
    assert not si._is_server_cmdline(["python", "-m", "mememo", "session-start", "--hook"])
    assert not si._is_server_cmdline(["python", "-m", "mememo", "index", "."])
    assert not si._is_server_cmdline(["python", "-m", "mememo", "serve"])
    assert not si._is_server_cmdline(["mememo", "curate-skills"])
    assert not si._is_server_cmdline([])
    assert not si._is_server_cmdline(["python", "-m", "other"])


# --- controlling-client resolution ------------------------------------------


def test_client_pid_walks_past_venv_launcher(monkeypatch):
    """worker -> launcher(python) -> claude.exe resolves to the claude PID."""
    procs = [_client(), _FakeProc(50, ["python", "-m", "mememo"], 2.0, ppid=CLIENT)]
    worker = _FakeProc(51, ["python", "-m", "mememo"], 2.1, ppid=50)
    procs.append(worker)
    ps = _fake_psutil(procs)
    assert si._client_pid(51, ps) == CLIENT


def test_client_pid_none_when_parent_dead(monkeypatch):
    orphan = _FakeProc(51, ["python", "-m", "mememo"], 2.0, ppid=999)  # 999 not registered
    ps = _fake_psutil([orphan])
    assert si._client_pid(51, ps) is None


def test_client_pid_walks_past_versioned_python():
    """A versioned interpreter name (Linux venvs surface ``python3.12``) is still
    recognised as part of the launch chain and walked past to the real client."""
    procs = [
        _client(),
        _FakeProc(60, ["python3.12", "-m", "mememo"], 2.0, ppid=CLIENT, name="python3.12"),
        _FakeProc(61, ["python3.12", "-m", "mememo"], 2.1, ppid=60, name="python3.12"),
    ]
    ps = _fake_psutil(procs)
    assert si._client_pid(61, ps) == CLIENT


# --- reap_orphan_servers ----------------------------------------------------


def test_reap_kills_older_same_session_server(monkeypatch):
    orphan = _server(111, create_time=100.0)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([_client(), orphan]))
    n = si.reap_orphan_servers(own_create_time=200.0, own_client=CLIENT)  # 100s older
    assert n == 1 and orphan.terminated


def test_reap_spares_other_session_server(monkeypatch):
    """The regression guard: a live server under a DIFFERENT client (another
    concurrent Claude session) is never reaped, even on the same store."""
    other = _server(111, create_time=100.0, client=OTHER_CLIENT)
    procs = [_client(), _client(OTHER_CLIENT), other]
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil(procs))
    n = si.reap_orphan_servers(own_create_time=200.0, own_client=CLIENT)
    assert n == 0 and not other.terminated


def test_reap_noop_when_own_client_unresolved(monkeypatch):
    """If we can't resolve our own client, reap nothing (conservative)."""
    orphan = _server(111, create_time=100.0)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([_client(), orphan]))
    # own_client omitted; os.getpid() isn't in the fake registry -> unresolved.
    n = si.reap_orphan_servers(own_create_time=200.0)
    assert n == 0 and not orphan.terminated


def test_reap_spares_candidate_with_unresolvable_client(monkeypatch):
    """Conservative: a candidate whose OWN client can't be resolved (dead or
    unreadable parent) is spared, not reaped — sparing a possible leak beats
    risking a live sibling session. Mitigated in practice because a dead-client
    orphan's stdin pipe is closed, so it self-exits on EOF anyway."""
    orphan = _FakeProc(111, ["python", "-m", "mememo"], create_time=100.0, ppid=999)  # 999 absent
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([_client(), orphan]))
    n = si.reap_orphan_servers(own_create_time=200.0, own_client=CLIENT)
    assert n == 0 and not orphan.terminated


def test_reap_spares_own_launcher_ancestor(monkeypatch):
    """Our own venv launcher is same-client and old, but we descend from it, so
    it must never be reaped — only the unrelated orphan is."""
    monkeypatch.setattr(si.os, "getpid", lambda: 50)
    launcher = _FakeProc(51, ["python", "-m", "mememo"], create_time=1.0, ppid=CLIENT)
    me = _FakeProc(50, ["python", "-m", "mememo"], create_time=199.0, ppid=51)
    orphan = _server(111, create_time=1.0)
    procs = [_client(), launcher, me, orphan]
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil(procs))
    n = si.reap_orphan_servers(own_create_time=200.0)  # client resolved via getpid=50
    assert n == 1 and orphan.terminated and not launcher.terminated


def test_reap_spares_same_connection_peer(monkeypatch):
    """A server started ~30ms before us is a same-connect peer, not an orphan."""
    peer = _server(111, create_time=199.97)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([_client(), peer]))
    n = si.reap_orphan_servers(own_create_time=200.0, own_client=CLIENT)
    assert n == 0 and not peer.terminated


def test_reap_spares_younger_server(monkeypatch):
    younger = _server(111, create_time=500.0)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([_client(), younger]))
    assert si.reap_orphan_servers(own_create_time=200.0, own_client=CLIENT) == 0
    assert not younger.terminated


def test_reap_ignores_non_server_processes(monkeypatch):
    hook = _FakeProc(
        111, ["python", "-m", "mememo", "session-start", "--hook"], create_time=10.0, ppid=CLIENT
    )
    other = _FakeProc(222, ["python", "-m", "other"], create_time=10.0, ppid=CLIENT)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([_client(), hook, other]))
    assert si.reap_orphan_servers(own_create_time=200.0, own_client=CLIENT) == 0
    assert not hook.terminated and not other.terminated


def test_reap_excludes_self(monkeypatch):
    me = _server(os.getpid(), create_time=10.0)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([_client(), me]))
    assert si.reap_orphan_servers(own_create_time=200.0, own_client=CLIENT) == 0
    assert not me.terminated


def test_reap_noop_without_own_create_time(monkeypatch):
    orphan = _server(111, create_time=1.0)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([_client(), orphan]))
    monkeypatch.setattr(si, "_own_create_time", lambda: None)
    assert si.reap_orphan_servers(own_create_time=None) == 0
    assert not orphan.terminated


def test_reap_noop_without_psutil(monkeypatch):
    monkeypatch.setattr(si, "_psutil", lambda: None)
    assert si.reap_orphan_servers(own_create_time=200.0, own_client=CLIENT) == 0


def test_reap_spares_different_store_server(monkeypatch):
    """A same-client server pointed at a different MEMEMO_STORAGE_DIR doesn't
    contend, so it's left alone."""
    monkeypatch.delenv("MEMEMO_STORAGE_DIR", raising=False)  # we use the default store
    other = _server(111, create_time=10.0, env={"MEMEMO_STORAGE_DIR": "/some/other/store/data"})
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([_client(), other]))
    assert si.reap_orphan_servers(own_create_time=200.0, own_client=CLIENT) == 0
    assert not other.terminated


def test_reap_kills_same_store_server(monkeypatch, tmp_path):
    store = str(tmp_path / "data")
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", store)
    same = _server(111, create_time=10.0, env={"MEMEMO_STORAGE_DIR": store})
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([_client(), same]))
    assert si.reap_orphan_servers(own_create_time=200.0, own_client=CLIENT) == 1
    assert same.terminated


def test_reap_kills_when_store_unreadable(monkeypatch):
    """Unknown store (environ() raises) falls back to reaping — same client,
    same machine, almost certainly the same default store."""
    monkeypatch.delenv("MEMEMO_STORAGE_DIR", raising=False)

    class _Blind(_FakeProc):
        def environ(self):
            raise RuntimeError("access denied")

    orphan = _Blind(111, ["python", "-m", "mememo"], create_time=10.0, ppid=CLIENT)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([_client(), orphan]))
    assert si.reap_orphan_servers(own_create_time=200.0, own_client=CLIENT) == 1
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

    boom = _Exploding(111, ["python", "-m", "mememo"], create_time=10.0, ppid=CLIENT)
    good = _server(222, create_time=10.0)
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil([_client(), boom, good]))
    assert si.reap_orphan_servers(own_create_time=200.0, own_client=CLIENT) == 1
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


def test_live_sibling_servers_reports_same_session_only(monkeypatch):
    monkeypatch.setattr(si.os, "getpid", lambda: 50)
    procs = [
        _client(),
        _client(OTHER_CLIENT),
        _server(50, create_time=1.0),  # self
        _server(111, create_time=2.0),  # a same-session sibling
        _server(333, create_time=2.0, client=OTHER_CLIENT),  # another session — excluded
        _FakeProc(222, ["python", "-m", "mememo", "index", "."], create_time=3.0, ppid=CLIENT),
    ]
    monkeypatch.setattr(si, "_psutil", lambda: _fake_psutil(procs))
    pids = [s["pid"] for s in si.live_sibling_servers(own_client=CLIENT)]
    assert pids == [111]  # self, other-session, and the `index` subcommand all excluded


def test_live_sibling_servers_empty_without_psutil(monkeypatch):
    monkeypatch.setattr(si, "_psutil", lambda: None)
    assert si.live_sibling_servers() == []
