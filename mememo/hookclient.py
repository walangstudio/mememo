"""Client side of the hook sidecar — see mememo/hookd.py.

Reads ``~/.mememo/.daemon.json`` (or ``$MEMEMO_STORAGE_DIR/../.daemon.json``),
POSTs the hook payload to the running MCP server, prints its captured
stdout/stderr verbatim, and exits with the server-reported code.

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


def _discovery_path() -> Path:
    base = Path(os.environ.get("MEMEMO_STORAGE_DIR") or (Path.home() / ".mememo" / "data"))
    return base.parent / ".daemon.json"


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


def _load_discovery() -> dict:
    path = _discovery_path()
    if not path.exists():
        raise DaemonUnavailableError(f"no discovery file at {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise DaemonUnavailableError(f"bad discovery file: {e}") from e
    for key in ("pid", "port", "token"):
        if key not in data:
            raise DaemonUnavailableError(f"discovery file missing {key!r}")
    if not _pid_alive(int(data["pid"])):
        raise DaemonUnavailableError(f"daemon pid {data['pid']} not alive")
    if not _port_open(int(data["port"])):
        raise DaemonUnavailableError(f"daemon port {data['port']} not reachable")
    return data


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
