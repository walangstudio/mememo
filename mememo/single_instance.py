"""Single-instance guard for the MCP server.

On Windows especially, a Claude Code ``/mcp`` reconnect spawns a fresh mememo
server without reliably terminating the previous one: the orphan's stdin pipe
stays open, so ``mcp.run()`` never sees EOF and the process blocks forever. The
orphans pile up and contend for the single ``~/.mememo`` store, starving the new
server's init (the bounded-init error from v0.38.0 is the visible symptom).

This guard makes each new server, on startup, reap every *older* mememo server
that targets the *same store* so the pile can never grow. Two scoping rules keep
it from killing the wrong process:

* **Age** — a single connect can spawn two server processes milliseconds apart
  (e.g. a venv interpreter and the global one). Only servers that started more
  than a short same-connection window before us are reaped; a same-connection
  peer's stdio pipe is live and killing it would loop.
* **Store** — a server pointed at a *different* ``MEMEMO_STORAGE_DIR`` doesn't
  contend with us, so it's left alone. Only when we can positively confirm a
  different store do we skip; an unreadable environment falls back to reaping
  (same machine, almost certainly the same default store).

``server.pid`` records the current server for diagnostics, and
``live_sibling_servers()`` lets ``check_memory`` report same-store contenders.

Opt out of the terminate step with ``MEMEMO_NO_REAP=1`` (the pidfile is still
written and the sibling scan still runs; only the kill is skipped).
"""

from __future__ import annotations

import atexit
import contextlib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# A single Claude Code connect can spawn two server processes a few milliseconds
# apart. Only reap siblings that started MORE than this many seconds before us —
# anything closer is a same-connection peer whose stdio pipe is live, and killing
# it would drop the connection and loop. Human reconnects are always seconds apart.
_SAME_CONNECT_WINDOW = 3.0


def _pidfile_path() -> Path:
    base = Path(os.environ.get("MEMEMO_STORAGE_DIR") or (Path.home() / ".mememo" / "data"))
    base.parent.mkdir(parents=True, exist_ok=True)
    return base.parent / "server.pid"


def _resolve_store(val: str | None) -> str:
    """Canonical, case-normalised store path for comparison across processes."""
    base = Path(val) if val else (Path.home() / ".mememo" / "data")
    try:
        resolved = str(base.resolve())
    except Exception:
        resolved = str(base)
    return os.path.normcase(resolved)


def _my_store() -> str:
    return _resolve_store(os.environ.get("MEMEMO_STORAGE_DIR"))


def _is_server_cmdline(cmd: list[str]) -> bool:
    """True iff ``cmd`` is a bare ``mememo`` MCP-server invocation.

    Matches ``python -m mememo`` (nothing after the module) and the console-script
    form (``.../mememo[.exe]`` with no args). Deliberately excludes hook fast-paths
    (``... --hook``) and CLI subcommands (``index``/``serve``/...), which are
    short-lived (or intentionally long-lived, like ``serve``) and must never be reaped.
    """
    if not cmd:
        return False
    for i, tok in enumerate(cmd[:-1]):
        if tok == "-m" and cmd[i + 1] == "mememo":
            return len(cmd) == i + 2  # nothing follows the module token
    base = os.path.basename(cmd[0]).lower()
    if base == "mememo" or base == "mememo.exe":
        return len(cmd) == 1
    return False


def _psutil():
    try:
        import psutil

        return psutil
    except Exception:  # pragma: no cover - psutil is a hard dep, defensive only
        return None


def _own_create_time():
    ps = _psutil()
    if ps is None:
        return None
    with contextlib.suppress(Exception):
        return ps.Process(os.getpid()).create_time()
    return None


def _targets_my_store(proc, mine: str):
    """True/False if proc's store provably matches/differs from ``mine``; None if
    the process environment can't be read (treat as unknown)."""
    try:
        env = proc.environ()
    except Exception:
        return None
    return _resolve_store(env.get("MEMEMO_STORAGE_DIR")) == mine


def _read(path: Path) -> dict | None:
    with contextlib.suppress(OSError, json.JSONDecodeError):
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def _write(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with contextlib.suppress(OSError):
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)


def _clear_if_ours(path: Path) -> None:
    data = _read(path)
    if data and int(data.get("pid", -1)) == os.getpid():
        with contextlib.suppress(FileNotFoundError, OSError):
            path.unlink()


def _terminate(proc) -> bool:
    """Terminate ``proc``; returns True if it is gone afterward (incl. already-dead)."""
    ps = _psutil()
    gone = getattr(ps, "NoSuchProcess", ()) if ps is not None else ()
    try:
        proc.terminate()
    except gone:
        return True  # already exited between scan and terminate — counts as reaped
    except Exception:
        logger.warning("could not reap mememo server pid=%s", getattr(proc, "pid", "?"))
        return False
    with contextlib.suppress(Exception):
        proc.wait(timeout=3)
    with contextlib.suppress(Exception):
        if proc.is_running():
            proc.kill()
    return True


def reap_orphan_servers(own_create_time=None) -> int:
    """Terminate every same-store mememo server that started before this one
    (minus the same-connection window). Returns the number reaped.

    Iterating live processes (rather than trusting a recorded PID) makes this
    robust to a connect spawning multiple server processes: each is matched by
    its own live cmdline, so PID reuse can't mislead us and a leaked peer can't
    survive just because it wasn't the one in the pidfile.
    """
    ps = _psutil()
    if ps is None:
        return 0
    own_ct = own_create_time if own_create_time is not None else _own_create_time()
    if own_ct is None:
        # Without our own start time we can't tell an orphan from a live peer.
        return 0
    mine = _my_store()
    me = os.getpid()
    reaped = 0
    for proc in ps.process_iter(["pid", "cmdline", "create_time"]):
        try:
            info = proc.info
            pid = info.get("pid")
            if pid == me or pid is None:
                continue
            ct = info.get("create_time")
            if ct is None or (own_ct - ct) <= _SAME_CONNECT_WINDOW:
                continue  # younger than us, or a same-connection peer
            if not _is_server_cmdline(info.get("cmdline") or []):
                continue
            if _targets_my_store(proc, mine) is False:
                continue  # a different store — it doesn't contend with us
            if _terminate(proc):
                reaped += 1
                logger.warning(
                    "reaped leaked mememo server pid=%d (orphaned by a previous reconnect)", pid
                )
        except Exception:
            # A process can vanish or deny access mid-iteration; never let one
            # bad entry abort the whole sweep.
            continue
    return reaped


def claim_singleton(version: str = "unknown") -> None:
    """Reap older same-store orphaned mememo servers, then record this process.

    Called once at server startup. Failures are non-fatal: the guard is a
    self-healing optimisation, never a boot blocker.
    """
    try:
        own_ct = _own_create_time()
        if os.environ.get("MEMEMO_NO_REAP") != "1":
            reap_orphan_servers(own_ct)
        path = _pidfile_path()
        _write(path, {"pid": os.getpid(), "create_time": own_ct, "version": version})
        atexit.register(lambda p=path: _clear_if_ours(p))
    except Exception:  # pragma: no cover - never block boot on the guard
        logger.exception("single-instance guard failed (continuing)")


def live_sibling_servers() -> list[dict]:
    """Other live same-store mememo MCP-server processes (excluding self).

    Returns ``[{"pid", "exe", "create_time"}, ...]`` — the contenders for our
    store, for ``check_memory`` diagnostics. Empty when psutil is unavailable or
    no siblings exist. A server pointed at a different store is excluded.
    """
    me = os.getpid()
    ps = _psutil()
    if ps is None:
        return []
    mine = _my_store()
    out: list[dict] = []
    for proc in ps.process_iter(["pid", "exe", "cmdline", "create_time"]):
        try:
            info = proc.info
            if info.get("pid") == me:
                continue
            if not _is_server_cmdline(info.get("cmdline") or []):
                continue
            if _targets_my_store(proc, mine) is False:
                continue
            out.append(
                {
                    "pid": info.get("pid"),
                    "exe": info.get("exe"),
                    "create_time": info.get("create_time"),
                }
            )
        except Exception:
            continue
    return out
