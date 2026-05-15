"""Resolver benchmark (T027 / FR-016).

Constitution budget: resolve_edges MUST complete in <= 1.0 second per
10,000 chunks on a single core. This script builds a synthetic symbol
table of N qualnames + N raw edges (mix of exact, suffix-match, and
unresolvable), times resolve_edges, and reports the budget delta.

Invoke directly: ``python benchmarks/resolver_perf.py [--n 10000]``.
The pytest gate at tests/test_v05_perf_resolver.py uses this same harness.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mememo.chunking.base_chunker import RawEdge  # noqa: E402
from mememo.core.symbol_resolver import SymbolEntry, resolve_edges  # noqa: E402


SHA = "a" * 40
BUDGET_S_PER_10K = 1.0


def build_corpus(n: int) -> tuple[list[SymbolEntry], list[RawEdge]]:
    """Synthetic corpus: n symbols across m modules; n raw edges where
    roughly half are exact-qualname matches, a quarter suffix matches,
    and a quarter unresolvable (AMBIGUOUS).
    """
    symbols: list[SymbolEntry] = []
    per_module = max(10, n // 100)
    module_count = max(1, n // per_module)
    for m in range(module_count):
        module = f"pkg.mod_{m}"
        for c in range(min(per_module, n - len(symbols))):
            qual = f"{module}.Class_{c // 10}.method_{c}"
            symbols.append(SymbolEntry(memory_id=f"mem-{len(symbols)}", qualname=qual))
            if len(symbols) >= n:
                break
        if len(symbols) >= n:
            break

    edges: list[RawEdge] = []
    for i in range(n):
        src = symbols[i % len(symbols)].qualname
        if i % 4 == 0:
            # Unresolvable target.
            edges.append(RawEdge(src, f"unknown_target_{i}", "CALLS"))
        elif i % 4 == 1:
            # Suffix match: just the method name.
            tgt_sym = symbols[(i * 7) % len(symbols)]
            tail = tgt_sym.qualname.rsplit(".", 1)[-1]
            edges.append(RawEdge(src, tail, "CALLS"))
        else:
            # Exact match.
            tgt_sym = symbols[(i * 3) % len(symbols)]
            edges.append(RawEdge(src, tgt_sym.qualname, "CALLS"))
    return symbols, edges


def run(n: int) -> dict:
    symbols, edges = build_corpus(n)
    start = time.perf_counter()
    relations = resolve_edges(
        edges, repo_id="r", branch="main", commit_sha=SHA, symbols=symbols
    )
    elapsed = time.perf_counter() - start
    budget = BUDGET_S_PER_10K * (n / 10_000)
    return {
        "n_symbols": len(symbols),
        "n_raw_edges": len(edges),
        "n_resolved": sum(1 for r in relations if r.confidence != "AMBIGUOUS"),
        "n_ambiguous": sum(1 for r in relations if r.confidence == "AMBIGUOUS"),
        "elapsed_s": elapsed,
        "budget_s": budget,
        "over_budget": elapsed > budget,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10_000)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = run(args.n)
    if args.json:
        import json as _json

        print(_json.dumps(result, indent=2))
    else:
        print(
            f"resolver_perf n={result['n_symbols']}: "
            f"{result['elapsed_s']:.3f}s elapsed / {result['budget_s']:.3f}s budget "
            f"({'OVER' if result['over_budget'] else 'within'} budget) — "
            f"resolved {result['n_resolved']}, ambiguous {result['n_ambiguous']}"
        )
    return 1 if result["over_budget"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
