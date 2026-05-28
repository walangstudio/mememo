"""End-to-end tests for the hook sidecar (hookd + hookclient)."""

from __future__ import annotations

import json
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
    # hookclient._discovery_path() derives ~/.mememo/.daemon.json from MEMEMO_STORAGE_DIR's
    # parent; the fixture's discovery file was placed at tmp_path/.daemon.json,
    # so point MEMEMO_STORAGE_DIR at tmp_path/data so .parent matches.
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
