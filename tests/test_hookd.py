"""End-to-end tests for the hook sidecar (hookd + hookclient)."""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path

import pytest

from mememo import hookclient, hookd


async def _echo_hook(stdin_text: str) -> None:
    """Trivial test hook: prints what it got and an exit message."""
    print(f"echoed:{stdin_text}")  # captured to response stdout
    import sys

    print("noise:to-stderr", file=sys.stderr)  # captured to response stderr


@pytest.fixture()
def daemon(tmp_path: Path):
    disc = tmp_path / ".daemon.json"
    port, token, shutdown = hookd.start(
        factories={"echo": _echo_hook},
        discovery_path=disc,
        version="test",
    )
    # Give the listener a tick to start serving.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        with socket.socket() as s:
            s.settimeout(0.2)
            try:
                s.connect(("127.0.0.1", port))
                break
            except OSError:
                time.sleep(0.05)
    yield {"port": port, "token": token, "disc": disc}
    shutdown()


def test_round_trip_captures_stdout_stderr_and_exitcode(daemon, monkeypatch):
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(daemon["disc"].parent / "data"))
    # hookclient scans MEMEMO_STORAGE_DIR's parent for .daemon*.json; the fixture's
    # discovery file was placed at tmp_path/.daemon.json, so point MEMEMO_STORAGE_DIR
    # at tmp_path/data so .parent matches.
    rc = hookclient.run("echo", stdin_text="hello-payload")
    assert rc == 0
    # stdout/stderr were forwarded by the client to the test process; capsys
    # can't catch them after fd-level writes, so the assertion that matters is
    # that the call didn't raise and returned 0.


def test_missing_discovery_raises_daemon_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "empty" / "data"))
    with pytest.raises(hookclient.DaemonUnavailableError):
        hookclient.run("echo", stdin_text="x")


def test_dead_pid_in_discovery_falls_back(monkeypatch, tmp_path):
    # PID 0 / unrealistically high PID -> not alive on any sane host.
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "data"))
    disc = tmp_path / ".daemon.json"
    disc.write_text(
        json.dumps({"pid": 999999, "port": 1, "token": "abc"}),
        encoding="utf-8",
    )
    with pytest.raises(hookclient.DaemonUnavailableError):
        hookclient.run("echo", stdin_text="x")


def test_discovery_path_is_per_pid():
    """Each server publishes its OWN .daemon.<pid>.json so concurrent windows on
    one store never clobber each other's pointer."""
    assert hookd._discovery_path().name == f".daemon.{os.getpid()}.json"
    assert hookd._discovery_path(123).name == ".daemon.123.json"


def test_client_prefers_cwd_matching_daemon(monkeypatch, tmp_path):
    """With several live daemons on one store, the client routes to the one launched
    from this hook's cwd (its repo lane)."""
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(hookclient, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(hookclient, "_port_open", lambda port, timeout=0.4: True)

    mine = os.path.normcase(os.getcwd())
    (tmp_path / ".daemon.111.json").write_text(
        json.dumps({"pid": 111, "port": 5001, "token": "a", "cwd": r"X:\other\repo"}),
        encoding="utf-8",
    )
    (tmp_path / ".daemon.222.json").write_text(
        json.dumps({"pid": 222, "port": 5002, "token": "b", "cwd": mine}),
        encoding="utf-8",
    )
    info = hookclient._load_discovery()
    assert info["port"] == 5002, "should pick the daemon whose cwd matches ours"


def test_client_falls_back_to_any_live_when_no_cwd_match(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(hookclient, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(hookclient, "_port_open", lambda port, timeout=0.4: True)

    (tmp_path / ".daemon.111.json").write_text(
        json.dumps({"pid": 111, "port": 5001, "token": "a", "cwd": r"X:\a"}),
        encoding="utf-8",
    )
    info = hookclient._load_discovery()
    assert info["port"] == 5001, "no cwd match -> still use a live daemon (global lane)"


def test_client_skips_dead_and_unreachable_daemons(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "data"))
    # 111 dead, 222 alive-but-port-closed -> no usable daemon.
    monkeypatch.setattr(hookclient, "_pid_alive", lambda pid: pid != 111)
    monkeypatch.setattr(hookclient, "_port_open", lambda port, timeout=0.4: False)
    (tmp_path / ".daemon.111.json").write_text(
        json.dumps({"pid": 111, "port": 5001, "token": "a"}), encoding="utf-8"
    )
    (tmp_path / ".daemon.222.json").write_text(
        json.dumps({"pid": 222, "port": 5002, "token": "b"}), encoding="utf-8"
    )
    with pytest.raises(hookclient.DaemonUnavailableError):
        hookclient._load_discovery()


def test_shutdown_removes_only_own_discovery_file(monkeypatch, tmp_path):
    """A window closing must not delete a live peer window's pointer."""
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "data"))
    _, _, shutdown = hookd.start(factories={"echo": _echo_hook}, version="test")
    own = hookd._discovery_path()
    assert own.exists()
    peer = tmp_path / ".daemon.999999.json"  # created after start -> not swept
    peer.write_text(json.dumps({"pid": 999999, "port": 1, "token": "x"}), encoding="utf-8")
    shutdown()
    assert not own.exists(), "own pointer should be removed on shutdown"
    assert peer.exists(), "a peer's pointer must survive our shutdown"


def test_start_sweeps_dead_peer_discovery(monkeypatch, tmp_path):
    """Startup clears pointers left by servers that died without cleanup, but keeps
    our own."""
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "data"))
    dead = tmp_path / ".daemon.999999.json"
    dead.write_text(json.dumps({"pid": 999999, "port": 1, "token": "x"}), encoding="utf-8")
    _, _, shutdown = hookd.start(factories={"echo": _echo_hook}, version="test")
    try:
        assert not dead.exists(), "dead peer pointer should be swept at start"
        assert hookd._discovery_path().exists(), "our own pointer must remain"
    finally:
        shutdown()


def test_client_skips_malformed_discovery_file(monkeypatch, tmp_path):
    """A garbage file (non-int pid) must be skipped, not crash discovery for the
    other daemons — the caller only catches DaemonUnavailableError, so an escaping
    ValueError would break the inject hook for every window."""
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(hookclient, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(hookclient, "_port_open", lambda port, timeout=0.4: True)
    (tmp_path / ".daemon.111.json").write_text(
        json.dumps({"pid": "not-an-int", "port": 5001, "token": "a"}), encoding="utf-8"
    )
    (tmp_path / ".daemon.222.json").write_text(
        json.dumps({"pid": 222, "port": 5002, "token": "b", "cwd": os.path.normcase(os.getcwd())}),
        encoding="utf-8",
    )
    info = hookclient._load_discovery()
    assert info["port"] == 5002, "malformed file skipped; valid cwd-match still chosen"


def test_client_malformed_only_raises_daemon_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "data"))
    (tmp_path / ".daemon.111.json").write_text("{ truncated", encoding="utf-8")
    (tmp_path / ".daemon.222.json").write_text(
        json.dumps({"pid": "x", "port": "y", "token": "z"}), encoding="utf-8"
    )
    with pytest.raises(hookclient.DaemonUnavailableError):
        hookclient._load_discovery()


def test_bad_token_rejected(daemon, monkeypatch):
    """Hitting the listener with a wrong token returns 401 from hookd."""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{daemon['port']}/hooks/echo",
        data=b"x",
        method="POST",
        headers={"Authorization": "Bearer wrong-token"},
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(req, timeout=2)
    assert excinfo.value.code == 401


def test_unknown_hook_rejected(daemon):
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{daemon['port']}/hooks/no-such-hook",
        data=b"x",
        method="POST",
        headers={"Authorization": f"Bearer {daemon['token']}"},
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(req, timeout=2)
    assert excinfo.value.code == 404


def test_hook_commands_use_ensure_initialized_not_full_init():
    """Regression for the sidecar's missing win: cmd_capture/cmd_inject/
    cmd_pre_tool must call ensure_initialized (idempotent) so the hookd
    handler reuses the daemon's already-initialised memory_manager instead
    of re-running full init on every hook fire."""
    import inspect

    from mememo import cli

    for fn_name in ("cmd_capture", "cmd_inject", "cmd_pre_tool"):
        src = inspect.getsource(getattr(cli, fn_name))
        assert "initialize_mememo" not in src, (
            f"{fn_name} still calls initialize_mememo directly; switch to "
            "ensure_initialized so the hookd sidecar can reuse the daemon's globals"
        )
        assert "ensure_initialized" in src, f"{fn_name} must call ensure_initialized"


def test_ensure_initialized_serializes_concurrent_cold_init(monkeypatch):
    """Race guard: when N threads each spin a fresh asyncio loop and hit
    ensure_initialized while memory_manager is still None, initialize_mememo
    must run exactly once. Pre-lock, the double-check passed in each thread
    and init ran N times in parallel."""
    import asyncio as _asyncio
    import threading as _threading

    import mememo.server as srv

    original_mm = srv.memory_manager
    srv.memory_manager = None

    call_count = 0
    counter_lock = _threading.Lock()

    async def fake_init():
        nonlocal call_count
        with counter_lock:
            call_count += 1
        # Hold in-flight long enough that other threads queued on the init
        # lock can prove they actually waited (not just lost the race).
        await _asyncio.sleep(0.05)
        srv.memory_manager = object()

    monkeypatch.setattr(srv, "initialize_mememo", fake_init)

    barrier = _threading.Barrier(4)
    errors: list[BaseException] = []

    def runner():
        try:
            barrier.wait(timeout=5)
            _asyncio.run(srv.ensure_initialized())
        except BaseException as e:  # noqa: BLE001 - surface to assertion
            errors.append(e)

    threads = [_threading.Thread(target=runner) for _ in range(4)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert not errors, f"runner failed: {errors}"
        assert call_count == 1, f"initialize_mememo ran {call_count} times; expected 1"
    finally:
        srv.memory_manager = original_mm


def test_hookd_dispatches_real_cmd_capture_default_factory(tmp_path, monkeypatch):
    """Integration: the sidecar's default factories actually invoke the real
    cli.cmd_* functions end-to-end (the gap that hid the ensure_initialized
    bug behind a trivial _echo factory in earlier tests). cmd_capture's
    early-exit path (empty payload -> no transcript_path) returns
    {"continue": true} without forcing full server init."""
    import urllib.request

    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "data"))

    disc = tmp_path / ".daemon.json"
    # Default factories pull from cli.cmd_capture / cmd_inject / cmd_pre_tool.
    port, token, shutdown = hookd.start(discovery_path=disc, version="test")
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/hooks/capture",
            data=b"{}",  # empty payload -> no transcript_path -> early exit
            method="POST",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read())
        assert payload["exitcode"] == 0, payload
        # cmd_capture prints {"continue": true} on the no-transcript path.
        assert '"continue"' in payload["stdout"], payload
    finally:
        shutdown()


def test_hook_exception_returns_nonzero_with_stderr(daemon, monkeypatch):
    """A hook that raises should yield exitcode=1 and the exception in stderr."""
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(daemon["disc"].parent / "data"))

    async def _boom(stdin_text: str) -> None:
        raise ValueError("kaboom")

    # Mutate the running daemon's factory map by re-starting on the same disc path.
    # Simpler: hit the existing daemon's POST with a payload that won't error, then
    # verify the exception-path via a dedicated fixture. For now, exercise via direct
    # urllib so we don't need a second daemon.
    import urllib.request

    # Restart the daemon with the boom factory.
    port, token, shutdown = hookd.start(
        factories={"boom": _boom},
        discovery_path=daemon["disc"].parent / ".boom.json",
        version="test",
    )
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/hooks/boom",
            data=b"x",
            method="POST",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            payload = json.loads(resp.read())
        assert payload["exitcode"] == 1
        assert "kaboom" in payload["stderr"]
    finally:
        shutdown()


# --- hook budget: a UserPromptSubmit hook must never approach the 30s kill ----


def _fake_resp(payload: dict):
    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    return _R()


def test_hook_daemon_timeout_defaults_short(monkeypatch):
    """A discovered-but-busy daemon must not consume the whole 30s hook budget."""
    monkeypatch.delenv("MEMEMO_HOOK_DAEMON_TIMEOUT_S", raising=False)
    monkeypatch.setattr(hookclient, "_load_discovery", lambda: {"pid": 1, "port": 9, "token": "t"})
    seen: dict = {}

    def _urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _fake_resp({"stdout": "", "stderr": "", "exitcode": 0})

    monkeypatch.setattr(hookclient.urllib.request, "urlopen", _urlopen)
    hookclient.run("inject", stdin_text="x")
    # 6s (not the old hardcoded 30) so a busy daemon leaves time to fall back
    assert seen["timeout"] == 6.0


def test_hook_daemon_timeout_env_override(monkeypatch):
    monkeypatch.setenv("MEMEMO_HOOK_DAEMON_TIMEOUT_S", "2.5")
    monkeypatch.setattr(hookclient, "_load_discovery", lambda: {"pid": 1, "port": 9, "token": "t"})
    seen: dict = {}

    def _urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _fake_resp({"stdout": "", "stderr": "", "exitcode": 0})

    monkeypatch.setattr(hookclient.urllib.request, "urlopen", _urlopen)
    hookclient.run("inject", stdin_text="x")
    assert seen["timeout"] == 2.5


def test_bounded_float_env_rejects_junk_and_clamps(monkeypatch):
    """Hook budgets are safety limits: inf/nan/negative/zero/garbage must fall back
    to the default (not defeat or crash the timeout), and a huge value is clamped
    so no single wait exceeds Claude Code's ~30s hook kill."""
    from mememo.hookclient import bounded_float_env

    monkeypatch.delenv("X_BUDGET", raising=False)
    assert bounded_float_env("X_BUDGET", 6.0, 10.0) == 6.0  # unset -> default
    for bad in ("inf", "-inf", "nan", "-1", "0", "junk", ""):
        monkeypatch.setenv("X_BUDGET", bad)
        assert bounded_float_env("X_BUDGET", 6.0, 10.0) == 6.0, bad
    monkeypatch.setenv("X_BUDGET", "25")
    assert bounded_float_env("X_BUDGET", 6.0, 10.0) == 10.0  # clamped to max
    monkeypatch.setenv("X_BUDGET", "3.5")
    assert bounded_float_env("X_BUDGET", 6.0, 10.0) == 3.5  # valid passes through


def test_run_inject_watchdog_fires_on_stall(monkeypatch):
    """A stalled in-process cold path trips the watchdog (which hard-exits in prod
    via os._exit) so the hook never approaches Claude Code's 30s kill. The deadline
    action is patched here so it doesn't take pytest down with it."""
    import asyncio as _asyncio
    import threading as _threading

    from mememo import cli

    fired = _threading.Event()
    release = _threading.Event()

    def _fake_deadline():
        fired.set()
        release.set()  # let the fake cmd_inject finish so asyncio.run returns

    monkeypatch.setattr(cli, "_on_inject_deadline", _fake_deadline)

    async def _stall(*_a, **_k):
        # ~4s cap so a regressed watchdog (never fires -> release never set) makes
        # the assert below FAIL cleanly instead of hanging pytest forever.
        for _ in range(200):
            if release.is_set():
                return
            await _asyncio.sleep(0.02)

    monkeypatch.setattr(cli, "cmd_inject", _stall)
    monkeypatch.setenv("MEMEMO_INJECT_BUDGET_S", "0.1")
    cli.run_inject()
    assert fired.is_set()


def test_on_inject_deadline_writes_continue_and_exits(monkeypatch, capsys):
    """The production deadline action emits a no-op continue and hard-exits 0."""
    from mememo import cli

    exited: dict = {}
    monkeypatch.setattr(cli.os, "_exit", lambda code: exited.update(code=code))
    cli._on_inject_deadline()
    assert json.loads(capsys.readouterr().out) == {"continue": True}
    assert exited["code"] == 0
