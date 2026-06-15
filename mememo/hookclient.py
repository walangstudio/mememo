"""Client side of the hook sidecar — see mememo/hookd.py.

Scans the per-server ``.daemon*.json`` pointers in ``~/.mememo`` (or
``$MEMEMO_STORAGE_DIR/..``), picks the live daemon launched from this hook's cwd,
POSTs the hook payload to that MCP server, prints its captured stdout/stderr
verbatim, and exits with the server-reported code.

If anything about the daemon is unreachable (file missing, pid dead, port
refused), raises :class:`DaemonUnavailableError` so the caller can fall back to the
slow path (``await initialize_mememo()`` in this process).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path


class DaemonUnavailableError(RuntimeError):
    """Raised when the sidecar can't be reached and the caller should fall back."""


def _discovery_dir() -> Path:
    base = Path(os.environ.get("MEMEMO_STORAGE_DIR") or (Path.home() / ".mememo" / "data"))
    return base.parent


def _candidate_discovery_files() -> list[Path]:
    """Every published hookd pointer in the store dir.

    Matches both the per-server ``.daemon.<pid>.json`` and the legacy single
    ``.daemon.json`` (back-compat with a pre-upgrade server still running)."""
    return sorted(_discovery_dir().glob(".daemon*.json"))


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # Cheap check via ctypes; avoid pulling psutil just for this.
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000  # noqa: N806 - Win32 API constant
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            STILL_ACTIVE = 259  # noqa: N806 - Win32 API constant
            return exit_code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


def _port_open(port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _read_discovery(path: Path) -> dict | None:
    """Parse one discovery file; None if unreadable, malformed, missing required
    keys, or pid/port aren't integers. A garbage file (truncated, or a foreign
    schema with a string pid) must be skipped — never crash discovery for every
    other daemon, which the caller only guards against via DaemonUnavailableError."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not all(k in data for k in ("pid", "port", "token")):
        return None
    try:
        data["pid"] = int(data["pid"])
        data["port"] = int(data["port"])
    except (TypeError, ValueError):
        return None
    return data


def _load_discovery() -> dict:
    """Pick a live hookd to talk to.

    Scans every ``.daemon*.json`` in the store dir, keeps the ones whose process is
    alive and whose port answers, and prefers the daemon launched from the same cwd
    as this hook process — that's the server holding our repo lane. Falls back to any
    live daemon (the global lane is shared), else raises so the caller uses the slow
    path.
    """
    files = _candidate_discovery_files()
    if not files:
        raise DaemonUnavailableError(f"no discovery file in {_discovery_dir()}")
    my_cwd = os.path.normcase(os.getcwd())
    live: list[dict] = []
    for path in files:
        data = _read_discovery(path)
        if data is None:
            continue
        if not _pid_alive(data["pid"]):  # already coerced to int by _read_discovery
            continue
        if not _port_open(data["port"]):
            continue
        if os.path.normcase(str(data.get("cwd", ""))) == my_cwd:
            return data  # exact repo-lane match — the server for this window
        live.append(data)
    if live:
        return live[0]
    raise DaemonUnavailableError(f"no live daemon among {len(files)} discovery file(s)")


def run(hook_name: str, stdin_text: str | None = None) -> int:
    """Send the hook to the running daemon and pipe the response back.

    Returns the server-reported exit code. Raises :class:`DaemonUnavailableError`
    if the daemon can't be reached.
    """
    info = _load_discovery()
    body = (stdin_text if stdin_text is not None else sys.stdin.read()).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{info['port']}/hooks/{hook_name}",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {info['token']}",
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(body)),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise DaemonUnavailableError(f"daemon call failed: {e}") from e
    except json.JSONDecodeError as e:
        raise DaemonUnavailableError(f"daemon returned non-JSON: {e}") from e

    sys.stdout.write(payload.get("stdout", ""))
    sys.stderr.write(payload.get("stderr", ""))
    return int(payload.get("exitcode", 0))
