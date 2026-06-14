"""Single-instance guard for the MCP server.

On Windows especially, a Claude Code ``/mcp`` reconnect spawns a fresh mememo
server without reliably terminating the previous one: the orphan's stdin pipe
stays open, so ``mcp.run()`` never sees EOF and the process blocks forever. The
orphans pile up and contend for the single ``~/.mememo`` store, starving the new
server's init (the bounded-init error from v0.38.0 is the visible symptom).

This guard makes each new server, on startup, reap every *older* mememo server
that belongs to the *same Claude session* so the pile can never grow. Three
scoping rules keep it from killing the wrong process — above all a *different*
concurrent Claude session's live server, which shares our store but is not ours
to kill:

* **Client** — the deciding rule. A ``/mcp`` reconnect re-spawns the server under
  the *same* controlling client (the ``claude``/``node`` process that owns the
  MCP stdio); a different window is a different client PID. We walk past the venv
  launcher (also Python) to that nearest non-Python ancestor and reap only
  servers that share ours. If we cannot resolve our own client we reap nothing —
  a leaked orphan lingering is far cheaper than killing a live sibling session.
* **Age** — a single connect can spawn two server processes milliseconds apart
  (the venv launcher and the worker). Only servers older than a short
  same-connection window are eligible, and our own ancestors are always spared,
  so we never reap the launcher we are running under.
* **Store** — a server pointed at a *different* ``MEMEMO_STORAGE_DIR`` doesn't
  contend with us, so it's left alone even within our client.

``server.pid`` records the current server for diagnostics, and
``live_sibling_servers()`` lets ``check_memory`` report same-session contenders.

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


# Extra (non ``python*``-prefixed) process names that still belong to a mememo
# launch chain and must be walked PAST to reach the real client (claude/node).
_PY_LAUNCHER_NAMES = frozenset({"py", "py.exe", "pyw", "pyw.exe"})


def _is_python_proc(name: str) -> bool:
    """True for any interpreter/launcher in a mememo launch chain — the venv
    launcher and the worker. Prefix match so versioned names (``python3.12``,
    ``python3.13.exe``) and the bare/``.exe`` forms are all covered; the real
    controlling client (``claude``/``node``/``Code``) never starts with ``python``."""
    n = (name or "").lower()
    return n.startswith("python") or n in _PY_LAUNCHER_NAMES


def _proc(ps, pid):
    with contextlib.suppress(Exception):
        return ps.Process(pid)
    return None


def _client_pid(pid, ps):
    """PID of the nearest non-Python ancestor — the MCP client (``claude``/``node``)
    that spawned this server, walking past the venv launcher. ``None`` if the chain
    can't be resolved (a dead or unreadable parent). Two servers share a Claude
    session iff this returns the same PID for both."""
    cur = _proc(ps, pid)
    if cur is None:
        return None
    seen = {pid}
    for _ in range(16):  # bounded — real launch chains are 2-3 deep
        try:
            parent = cur.parent()
        except Exception:
            return None
        if parent is None or parent.pid in seen:
            return None
        seen.add(parent.pid)
        try:
            name = parent.name()
        except Exception:
            return None
        if not _is_python_proc(name):
            return parent.pid
        cur = parent
    return None


def _ancestor_pids(pid, ps) -> set[int]:
    """All ancestor PIDs of ``pid`` so we never reap a process we descend from
    (most importantly the venv launcher that spawned us)."""
    out: set[int] = set()
    cur = _proc(ps, pid)
    if cur is None:
        return out
    for _ in range(16):
        try:
            parent = cur.parent()
        except Exception:
            break
        if parent is None or parent.pid in out:
            break
        out.add(parent.pid)
        cur = parent
    return out


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


def reap_orphan_servers(own_create_time=None, own_client=None) -> int:
    """Terminate every *same-session* mememo server that started before this one
    (minus the same-connection window). Returns the number reaped.

    "Same session" = sharing our controlling client PID. A *different* concurrent
    Claude session's server has a different client and is never touched, even
    though it shares our store. Iterating live processes (rather than trusting a
    recorded PID) makes this robust to a connect spawning multiple server
    processes: each is matched by its own live cmdline, so PID reuse can't mislead
    us and a leaked peer can't survive just because it wasn't in the pidfile.
    """
    ps = _psutil()
    if ps is None:
        return 0
    own_ct = own_create_time if own_create_time is not None else _own_create_time()
    if own_ct is None:
        # Without our own start time we can't tell an orphan from a live peer.
        return 0
    me = os.getpid()
    my_client = own_client if own_client is not None else _client_pid(me, ps)
    if my_client is None:
        # Can't establish our own controlling client → refuse to reap anything,
        # rather than risk killing a different live session that shares our store.
        return 0
    my_ancestors = _ancestor_pids(me, ps)
    mine = _my_store()
    reaped = 0
    for proc in ps.process_iter(["pid", "cmdline", "create_time"]):
        try:
            info = proc.info
            pid = info.get("pid")
            if pid is None or pid == me or pid in my_ancestors:
                continue  # never reap ourselves or a process we descend from
            ct = info.get("create_time")
            if ct is None or (own_ct - ct) <= _SAME_CONNECT_WINDOW:
                continue  # younger than us, or a same-connection peer
            if not _is_server_cmdline(info.get("cmdline") or []):
                continue
            if _client_pid(pid, ps) != my_client:
                continue  # a different Claude session's live server — leave it alone
            if _targets_my_store(proc, mine) is False:
                continue  # a different store — it doesn't contend with us
            if _terminate(proc):
                reaped += 1
                logger.warning(
                    "reaped leaked mememo server pid=%d "
                    "(orphaned by a previous reconnect of this session)",
                    pid,
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


def live_sibling_servers(own_client=None) -> list[dict]:
    """Other live *same-session* mememo MCP-server processes (excluding self and
    our own launcher).

    Returns ``[{"pid", "exe", "create_time"}, ...]`` — genuine contenders for our
    store from this same Claude session (leaked reconnect orphans), for
    ``check_memory`` diagnostics. Scoped to our controlling client, so a
    *different* concurrent Claude session's server is not reported as a problem.
    Empty when psutil is unavailable, our client can't be resolved, or none exist.
    """
    ps = _psutil()
    if ps is None:
        return []
    me = os.getpid()
    my_client = own_client if own_client is not None else _client_pid(me, ps)
    if my_client is None:
        return []
    my_ancestors = _ancestor_pids(me, ps)
    mine = _my_store()
    out: list[dict] = []
    for proc in ps.process_iter(["pid", "exe", "cmdline", "create_time"]):
        try:
            info = proc.info
            pid = info.get("pid")
            if pid is None or pid == me or pid in my_ancestors:
                continue
            if not _is_server_cmdline(info.get("cmdline") or []):
                continue
            if _client_pid(pid, ps) != my_client:
                continue
            if _targets_my_store(proc, mine) is False:
                continue
            out.append(
                {
                    "pid": pid,
                    "exe": info.get("exe"),
                    "create_time": info.get("create_time"),
                }
            )
        except Exception:
            continue
    return out
