"""Index-time benchmark (T028 / FR-035).

Constitution budget: chunk_with_edges (chunking + edge emission) MUST NOT
add more than 30% wall-time over chunk-only baseline on the synthetic
corpus. This script generates N synthetic Python files (each ~150 LOC
with imports, classes, methods, calls), times both modes, and reports
the delta.

Invoke: ``python benchmarks/index_corpus_perf.py [--files 200]``.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mememo.chunking.python_ast_chunker import PythonASTChunker  # noqa: E402


BUDGET_OVERHEAD_RATIO = 1.30


_PY_TEMPLATE = '''\
"""Synthetic module {idx}."""

from pkg.support_{idx_mod_5} import helper_{idx_mod_5}, Util_{idx_mod_5}
import json
import logging

logger = logging.getLogger(__name__)


@decorator
class Service_{idx}(BaseService_{idx_mod_3}):
    """Doc for Service_{idx}."""

    def __init__(self):
        self.cache = {{}}
        self.logger = logger

    def fetch(self, key):
        self.logger.info("fetch start")
        result = helper_{idx_mod_5}(key, self.cache)
        if not result:
            raise ValueError("missing")
        return Util_{idx_mod_5}.normalize(result)

    def save(self, key, value):
        self.cache[key] = value
        json.dumps(value)
        helper_{idx_mod_5}(key, self.cache)
        self.logger.debug("saved")
'''


def build_corpus(n_files: int) -> list[tuple[str, str]]:
    """Returns list of (file_path, source_code) tuples."""
    out: list[tuple[str, str]] = []
    for i in range(n_files):
        src = _PY_TEMPLATE.format(idx=i, idx_mod_5=i % 5, idx_mod_3=i % 3)
        out.append((f"pkg/module_{i}.py", src))
    return out


def run(n_files: int) -> dict:
    corpus = build_corpus(n_files)
    chunker = PythonASTChunker()

    # Baseline: chunk-only.
    t0 = time.perf_counter()
    baseline_chunks = 0
    for path, code in corpus:
        baseline_chunks += len(chunker.chunk(code, path))
    baseline_s = time.perf_counter() - t0

    # With edges: chunk_with_edges.
    t0 = time.perf_counter()
    edges_chunks = 0
    edges_emitted = 0
    for path, code in corpus:
        chunks, edges = chunker.chunk_with_edges(code, path)
        edges_chunks += len(chunks)
        edges_emitted += len(edges)
    edges_s = time.perf_counter() - t0

    overhead = (edges_s / baseline_s) if baseline_s > 0 else 0.0
    return {
        "n_files": n_files,
        "baseline_s": baseline_s,
        "edges_s": edges_s,
        "baseline_chunks": baseline_chunks,
        "edges_chunks": edges_chunks,
        "edges_emitted": edges_emitted,
        "overhead_ratio": overhead,
        "budget_ratio": BUDGET_OVERHEAD_RATIO,
        "over_budget": overhead > BUDGET_OVERHEAD_RATIO,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", type=int, default=200)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = run(args.files)
    if args.json:
        import json as _json

        print(_json.dumps(result, indent=2))
    else:
        print(
            f"index_corpus_perf files={result['n_files']}: "
            f"baseline {result['baseline_s']:.3f}s, "
            f"with-edges {result['edges_s']:.3f}s "
            f"(ratio {result['overhead_ratio']:.2f}x, budget {BUDGET_OVERHEAD_RATIO}x) "
            f"— {'OVER' if result['over_budget'] else 'within'} budget; "
            f"{result['edges_emitted']} edges emitted"
        )
    return 1 if result["over_budget"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
