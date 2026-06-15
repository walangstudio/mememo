"""Loopback sidecar for hook subcommands.

Claude Code fires `mememo capture|inject|pre-tool --hook` per turn. Each invocation
spawns a fresh Python process that runs ``initialize_mememo`` from scratch — ~3s after
the dimension-lookup fix, but still adds up to hundreds of cold inits per session.

This module runs a tiny loopback HTTP server inside the long-running MCP process so
hook CLI invocations can forward their stdin/stdout/stderr to the already-initialised
server. Falls back transparently to the slow path when the daemon isn't reachable
(e.g. git hooks firing outside Claude Code).

Wire characteristics:
    POST  http://127.0.0.1:<port>/hooks/<name>
    Headers: Authorization: Bearer <hex token>
    Body:    raw stdin bytes the CLI would receive
    Response (JSON):
        { "stdout": "<captured>", "stderr": "<captured>", "exitcode": <int> }

Security: bound to 127.0.0.1 only; bearer token written into the discovery file
with 0o600 perms; non-localhost connections refused at the socket level.
"""

from __future__ import annotations

import asyncio
import atexit
import contextlib
import io
import json
import logging
import os
import secrets
import socket
import sys
import threading
from collections.abc import Awaitable, Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logger = logging.getLogger(__name__)


# Hook name -> coroutine factory. Each factory accepts (stdin_text: str) and returns
# the awaitable that runs the hook. Hooks write to stdout/stderr; the wrapper
# captures both and returns them in the HTTP response.
HookFactory = Callable[[str], Awaitable[None]]


def _default_hook_factories() -> dict[str, HookFactory]:
    """Wrap mememo.cli.cmd_* so each takes the stdin payload as a string."""
    from . import cli as _cli

    def _wrap(coro_fn: Callable[[], Awaitable[None]]) -> HookFactory:
        async def _runner(stdin_text: str) -> None:
            # The hook commands read sys.stdin directly; swap it for the request body.
            with contextlib.redirect_stdout(io.StringIO()) as _:
                pass  # ensure import-time symbols are resolved before we patch stdin
            real_stdin = sys.stdin
            sys.stdin = io.StringIO(stdin_text)
            try:
                await coro_fn()
            finally:
                sys.stdin = real_stdin

        return _runner

    factories: dict[str, HookFactory] = {
        "capture": _wrap(_cli.cmd_capture),
        "inject": _wrap(_cli.cmd_inject),
        "pre-tool": _wrap(_cli.cmd_pre_tool),
    }

    # cmd_session_start lives in commands.session_start (Wave 2 wires it into
    # cli as well). Guard so hookd stays importable if Wave 2 hasn't landed yet.
    _cmd_session_start = getattr(_cli, "cmd_session_start", None)
    if _cmd_session_start is None:
        try:
            from .commands.session_start import cmd_session_start as _cmd_session_start
        except ImportError:
            _cmd_session_start = None

    if _cmd_session_start is not None:
        factories["session-start"] = _wrap(_cmd_session_start)

    return factories


def _discovery_dir() -> Path:
    base = Path(os.environ.get("MEMEMO_STORAGE_DIR") or (Path.home() / ".mememo" / "data"))
    base.parent.mkdir(parents=True, exist_ok=True)
    return base.parent


def _discovery_path(pid: int | None = None) -> Path:
    """Per-server discovery file ``.daemon.<pid>.json``.

    Each MCP server publishes its OWN file so concurrent Claude windows that share
    one store never clobber each other's hookd pointer. The old single
    ``.daemon.json`` meant the last server to start hijacked every window's inject
    hook (recalling the wrong repo lane), and its exit deleted the pointer the other
    live windows were still using. The client picks the live file whose ``cwd``
    matches its own, so each window's hook reaches the server holding its repo lane.
    """
    return _discovery_dir() / f".daemon.{os.getpid() if pid is None else pid}.json"


def _sweep_dead_discovery(keep: Path) -> None:
    """Best-effort removal of per-server discovery files whose process is gone (a
    server killed without running its atexit). Never touches a live peer's file."""
    from .hookclient import _pid_alive

    for p in _discovery_dir().glob(".daemon*.json"):  # incl. a stale legacy .daemon.json
        if p == keep:
            continue
        with contextlib.suppress(Exception):
            data = json.loads(p.read_text(encoding="utf-8"))
            if not _pid_alive(int(data.get("pid", -1))):
                p.unlink()


def _write_discovery(path: Path, payload: dict) -> None:
    """Atomic write so partial reads can't see a half-written file."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)  # best-effort; Windows profile ACLs already restrict.


def _make_handler(
    token: str,
    factories: dict[str, HookFactory],
) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        # Quiet by default; the MCP server's stderr is the MCP transport.
        def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
            logger.debug("hookd: " + format, *args)

        def _reject(self, code: int, msg: str) -> None:
            body = json.dumps({"error": msg}).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 - stdlib signature
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self._reject(HTTPStatus.FORBIDDEN, "loopback only")
                return
            auth = self.headers.get("Authorization", "")
            expected = f"Bearer {token}"
            if not secrets.compare_digest(auth, expected):
                self._reject(HTTPStatus.UNAUTHORIZED, "bad token")
                return
            if not self.path.startswith("/hooks/"):
                self._reject(HTTPStatus.NOT_FOUND, "unknown path")
                return
            name = self.path[len("/hooks/") :]
            factory = factories.get(name)
            if factory is None:
                self._reject(HTTPStatus.NOT_FOUND, f"unknown hook: {name}")
                return

            length = int(self.headers.get("Content-Length") or 0)
            stdin_text = self.rfile.read(length).decode("utf-8") if length else ""

            out_buf = io.StringIO()
            err_buf = io.StringIO()
            exitcode = 0
            try:
                # Each request gets its own loop in this handler thread; the hook
                # coroutine reuses the MCP server's already-initialised globals
                # (memory_manager, llm_adapter) — which are thread-safe for the
                # read-mostly access patterns these hooks have.
                with (
                    contextlib.redirect_stdout(out_buf),
                    contextlib.redirect_stderr(err_buf),
                ):
                    asyncio.run(factory(stdin_text))
            except SystemExit as e:
                exitcode = int(e.code) if isinstance(e.code, int) else 1
            except Exception as e:
                logger.exception("hookd: hook %r failed", name)
                err_buf.write(f"\nmememo hookd: {type(e).__name__}: {e}\n")
                exitcode = 1

            body = json.dumps(
                {"stdout": out_buf.getvalue(), "stderr": err_buf.getvalue(), "exitcode": exitcode}
            ).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return _Handler


def start(
    factories: dict[str, HookFactory] | None = None,
    discovery_path: Path | None = None,
    version: str = "unknown",
) -> tuple[int, str, Callable[[], None]]:
    """Start the loopback HTTP listener in a background daemon thread.

    Returns:
        (port, token, shutdown) — call ``shutdown()`` to stop the listener and
        remove the discovery file.
    """
    factories = factories if factories is not None else _default_hook_factories()
    disc = discovery_path or _discovery_path()
    token = secrets.token_hex(32)

    # Bind to an ephemeral port on loopback only.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.listen(16)

    handler_cls = _make_handler(token, factories)
    httpd = ThreadingHTTPServer.__new__(ThreadingHTTPServer)
    # Manual init to inject the pre-bound socket.
    ThreadingHTTPServer.__init__(httpd, ("127.0.0.1", port), handler_cls, bind_and_activate=False)
    httpd.socket = sock
    httpd.server_address = ("127.0.0.1", port)

    thread = threading.Thread(target=httpd.serve_forever, name="mememo-hookd", daemon=True)
    thread.start()

    _write_discovery(
        disc,
        {
            "pid": os.getpid(),
            "port": port,
            "token": token,
            "version": version,
            # Normalised launch cwd; the client routes by matching this so a window's
            # inject hook reaches the server holding its repo lane (see _discovery_path).
            "cwd": os.path.normcase(os.getcwd()),
        },
    )
    # Drop pointers left by sibling servers that died without cleanup so the dir
    # doesn't fill with stale files; never removes a live peer's pointer.
    with contextlib.suppress(Exception):
        _sweep_dead_discovery(keep=disc)
    logger.info("hookd listening on 127.0.0.1:%d (discovery: %s)", port, disc)

    def _shutdown() -> None:
        try:
            httpd.shutdown()
            httpd.server_close()
        finally:
            with contextlib.suppress(FileNotFoundError):
                disc.unlink()

    atexit.register(_shutdown)
    return port, token, _shutdown
