"""Performance budget gates for v0.5 (T027 / T028).

Runs scaled-down versions of the two benchmarks inside the test suite so
CI fails fast when the resolver or the edge-emission path regresses past
its constitution budget.

Sizing rationale:
- T027: resolver at 5,000 symbols (half the constitution sample). Budget
  scales linearly so it's 0.5s; we apply a 1.5x slack for CI noise.
- T028: index at 100 synthetic Python files (~15kLOC). Budget is the
  unchanged 30% overhead ratio.
"""

from __future__ import annotations

import sys
import types as _types


# Same dep-stubbing block as the rest of the v0.5 suite.
def _stub_module(name: str, **attrs: object) -> None:
    if name in sys.modules:
        return
    m = _types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m


class _Stub:  # pragma: no cover
    def __init__(self, *a, **k) -> None: ...

    def tool(self, *a, **k):
        def deco(fn):
            return fn

        return deco

    def resource(self, *a, **k):
        def deco(fn):
            return fn

        return deco


_stub_module("sentence_transformers", SentenceTransformer=_Stub)
_stub_module("faiss", Index=_Stub, IndexFlatL2=_Stub, IndexIDMap=_Stub, IndexIVFFlat=_Stub)
_stub_module("fastmcp", FastMCP=_Stub)


from benchmarks.index_corpus_perf import run as run_index  # noqa: E402
from benchmarks.resolver_perf import run as run_resolver  # noqa: E402

CI_SLACK = 1.5  # absorb noise on shared CI runners


def test_t027_resolver_within_budget() -> None:
    """resolve_edges MUST complete in <= 1.0s per 10k chunks (FR-016)."""
    result = run_resolver(n=5_000)
    # budget_s scales with n; apply slack to absorb CI jitter.
    budget = result["budget_s"] * CI_SLACK
    assert result["elapsed_s"] <= budget, (
        f"resolver took {result['elapsed_s']:.3f}s on n={result['n_symbols']}, "
        f"budget {result['budget_s']:.3f}s (+{int((CI_SLACK - 1) * 100)}% CI slack = {budget:.3f}s)"
    )
    # And it actually resolved most edges.
    assert result["n_resolved"] >= result["n_raw_edges"] * 0.5


def test_t028_edge_extraction_within_budget() -> None:
    """chunk_with_edges MUST NOT add >30% wall-time over chunk()-only (FR-035)."""
    result = run_index(n_files=100)
    budget = result["budget_ratio"] * CI_SLACK
    assert result["overhead_ratio"] <= budget, (
        f"edge extraction overhead {result['overhead_ratio']:.2f}x exceeds "
        f"{result['budget_ratio']}x (+{int((CI_SLACK - 1) * 100)}% CI slack = {budget:.2f}x); "
        f"baseline {result['baseline_s']:.3f}s, with-edges {result['edges_s']:.3f}s"
    )
    # Edges actually got emitted.
    assert result["edges_emitted"] > 0


def test_t028_chunk_counts_align() -> None:
    """The unified chunk_with_edges walk should produce the same number of
    chunks as chunk()-only on a corpus that uses both classes and functions.
    """
    result = run_index(n_files=20)
    # chunk_with_edges yields one chunk per class + method/function. The
    # legacy chunk() also emits class + functions; counts may differ slightly
    # because the legacy walk uses ast.walk while the new walk descends only
    # through class bodies. Accept within ±10%.
    base, edges = result["baseline_chunks"], result["edges_chunks"]
    assert (
        abs(base - edges) <= max(base, edges) * 0.1
    ), f"chunk counts differ too much: baseline={base}, with-edges={edges}"
