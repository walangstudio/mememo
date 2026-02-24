"""
mememo warmup - pre-warm bytecode cache and embedding model.

Run automatically after install/upgrade to ensure fast MCP startup.
On cold start Python must compile .py → .pyc for every imported module.
For heavy ML libraries (torch, sentence-transformers, faiss) this can
exceed the 30-second MCP connection timeout.

This script:
  1. Imports all heavy dependencies   → generates .pyc bytecode cache
  2. Downloads/caches the embedding model if not already present
  3. Runs a test encode               → fully initialises all C extensions
  4. Compileall on the mememo package → ensures our own code is cached too
"""

import sys
import time
import compileall
from pathlib import Path


def _step(msg: str) -> float:
    print(f"  {msg}", flush=True)
    return time.monotonic()


def warmup() -> None:
    print("mememo warmup: pre-warming for fast MCP startup...")
    t0 = time.monotonic()

    # ------------------------------------------------------------------
    # 1. Heavy imports  (each import compiles .py → .pyc on first run)
    # ------------------------------------------------------------------
    t = _step("Loading PyTorch ...")
    import torch  # noqa: F401 – side-effect import intentional

    print(f"    done ({time.monotonic() - t:.1f}s)", flush=True)

    t = _step("Loading sentence-transformers ...")
    from sentence_transformers import SentenceTransformer

    print(f"    done ({time.monotonic() - t:.1f}s)", flush=True)

    t = _step("Loading FAISS ...")
    import faiss  # noqa: F401

    print(f"    done ({time.monotonic() - t:.1f}s)", flush=True)

    t = _step("Loading tiktoken ...")
    import tiktoken  # noqa: F401

    print(f"    done ({time.monotonic() - t:.1f}s)", flush=True)

    # ------------------------------------------------------------------
    # 2. Compile mememo package bytecode
    # ------------------------------------------------------------------
    t = _step("Compiling mememo package ...")
    import mememo

    pkg_dir = Path(mememo.__path__[0])
    compileall.compile_dir(str(pkg_dir), quiet=1, force=True)
    print(f"    done ({time.monotonic() - t:.1f}s)", flush=True)

    # ------------------------------------------------------------------
    # 3. Download + warm the embedding model
    #    SentenceTransformer caches models under:
    #      ~/.cache/torch/sentence_transformers/   (Linux/macOS)
    #      %USERPROFILE%\.cache\torch\sentence_transformers\   (Windows)
    # ------------------------------------------------------------------
    t = _step("Loading embedding model (all-MiniLM-L6-v2, ~90 MB) ...")
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    print(f"    done ({time.monotonic() - t:.1f}s)", flush=True)

    t = _step("Running test encode ...")
    model.encode(["mememo warmup"], convert_to_numpy=True, show_progress_bar=False)
    print(f"    done ({time.monotonic() - t:.1f}s)", flush=True)

    total = time.monotonic() - t0
    print(f"Warmup complete in {total:.1f}s — mememo will start fast from now on.")


if __name__ == "__main__":
    try:
        warmup()
    except Exception as exc:
        print(f"[WARN] Warmup encountered an error: {exc}", file=sys.stderr)
        print("[WARN] mememo will still work; first startup may be slow.", file=sys.stderr)
        sys.exit(0)  # Non-fatal — don't break install
