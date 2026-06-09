"""Comparative benchmark: mememo vs the official MCP knowledge-graph memory server.

Drives both servers over real stdio JSON-RPC and measures the directly-comparable
axes: per-turn tool-definition token footprint, cold startup, and warm store / recall
latency. NOT a pytest test (network + Node required) — run manually:

    python tests/bench_vs_server_memory.py

Caveat: the two are not feature-equivalent. mememo is a code-aware *semantic* memory
(embeddings + AST indexing + graph + skills); @modelcontextprotocol/server-memory is a
keyword knowledge-graph (JSON file, substring search, no embeddings, no code indexing).
This measures cost/latency, not retrieval quality.
"""

from __future__ import annotations

import json
import os
import queue
import statistics
import subprocess
import tempfile
import threading
import time

try:
    import tiktoken

    _ENC = tiktoken.get_encoding("cl100k_base")

    def ntok(s: str) -> int:
        return len(_ENC.encode(s))

    TOK = "tiktoken cl100k"
except Exception:  # pragma: no cover

    def ntok(s: str) -> int:
        return len(s) // 4

    TOK = "chars/4"

MEMEMO_PY = r"F:\opt\projs\ai\claude\mememo\.venv\Scripts\python.exe"


class Server:
    """Minimal synchronous stdio JSON-RPC MCP client."""

    def __init__(self, argv, env=None):
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            env=env,
        )
        self._q: queue.Queue = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()
        self._id = 0

    def _reader(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._q.put(json.loads(line))
            except ValueError:
                pass

    def _send(self, obj):
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params=None, timeout=90):
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            try:
                msg = self._q.get(timeout=deadline - time.perf_counter())
            except queue.Empty:
                break
            if isinstance(msg, dict) and msg.get("id") == rid:
                return msg
        raise TimeoutError(f"{method} timed out")

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self):
        t0 = time.perf_counter()
        self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "bench", "version": "0"},
            },
        )
        self.notify("notifications/initialized")
        tools = self.request("tools/list")["result"]["tools"]
        ready = time.perf_counter() - t0
        return ready, tools

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def tool_tokens(tools):
    total = 0
    for t in tools:
        total += ntok(
            t.get("name", "")
            + "\n"
            + (t.get("description") or "")
            + "\n"
            + json.dumps(t.get("inputSchema", {}))
        )
    return total


def time_calls(srv, make_params, n=5):
    times = []
    for i in range(n):
        name, args = make_params(i)
        t0 = time.perf_counter()
        srv.request("tools/call", {"name": name, "arguments": args})
        times.append((time.perf_counter() - t0) * 1000)
    return statistics.median(times)


def bench_mememo():
    store = tempfile.mkdtemp(prefix="bench_mememo_")
    env = dict(os.environ, MEMEMO_STORAGE_DIR=store, NODE_OPTIONS="--use-system-ca")
    srv = Server([MEMEMO_PY, "-m", "mememo"], env=env)
    try:
        ready, tools = srv.initialize()
        # Warm the embedder (first embed loads the model).
        srv.request(
            "tools/call",
            {"name": "store_memory", "arguments": {"content": "warmup note", "type": "context"}},
        )
        srv.request(
            "tools/call", {"name": "search_similar", "arguments": {"query": "warm", "top_k": 5}}
        )
        store_ms = time_calls(
            srv,
            lambda i: (
                "store_memory",
                {
                    "content": f"decision number {i}: chose option {i} for reason {i}",
                    "type": "context",
                },
            ),
        )
        recall_ms = time_calls(
            srv, lambda i: ("search_similar", {"query": f"option {i}", "top_k": 5})
        )
        return {
            "name": "mememo",
            "tools": len(tools),
            "tool_tokens": tool_tokens(tools),
            "ready_s": ready,
            "store_ms": store_ms,
            "recall_ms": recall_ms,
        }
    finally:
        srv.close()


def _npx() -> str:
    # On Windows the bare ``npx`` is a PowerShell script CreateProcess can't launch;
    # use npx.cmd next to node. Falls back to PATH lookup on POSIX.
    import shutil

    cand = r"C:\Program Files\nodejs\npx.cmd"
    return cand if os.path.exists(cand) else (shutil.which("npx") or "npx")


def bench_server_memory():
    mem_file = tempfile.mktemp(prefix="bench_kg_", suffix=".json")
    env = dict(os.environ, MEMORY_FILE_PATH=mem_file, NODE_OPTIONS="--use-system-ca")
    srv = Server([_npx(), "-y", "@modelcontextprotocol/server-memory"], env=env)
    try:
        ready, tools = srv.initialize()
        srv.request(
            "tools/call",
            {
                "name": "create_entities",
                "arguments": {
                    "entities": [{"name": "warm", "entityType": "note", "observations": ["warmup"]}]
                },
            },
        )
        srv.request("tools/call", {"name": "search_nodes", "arguments": {"query": "warm"}})
        store_ms = time_calls(
            srv,
            lambda i: (
                "create_entities",
                {
                    "entities": [
                        {
                            "name": f"decision-{i}",
                            "entityType": "note",
                            "observations": [f"chose option {i} for reason {i}"],
                        }
                    ]
                },
            ),
        )
        recall_ms = time_calls(srv, lambda i: ("search_nodes", {"query": f"option {i}"}))
        return {
            "name": "server-memory",
            "tools": len(tools),
            "tool_tokens": tool_tokens(tools),
            "ready_s": ready,
            "store_ms": store_ms,
            "recall_ms": recall_ms,
        }
    finally:
        srv.close()


def main():
    print(f"token-mode: {TOK}\n")
    rows = []
    for fn in (bench_mememo, bench_server_memory):
        try:
            rows.append(fn())
        except Exception as e:
            print(f"{fn.__name__} failed: {type(e).__name__}: {e}")
    hdr = f"{'server':<16}{'tools':>6}{'tool_tokens':>13}{'startup_s':>11}{'store_ms':>10}{'recall_ms':>11}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['name']:<16}{r['tools']:>6}{r['tool_tokens']:>13}"
            f"{r['ready_s']:>11.2f}{r['store_ms']:>10.1f}{r['recall_ms']:>11.1f}"
        )


if __name__ == "__main__":
    main()
