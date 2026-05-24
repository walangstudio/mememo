"""Regression guard: the index edge pass must cover every walker language.

PRs #22-#25 added rust/java/c/cpp/csharp walkers to ts_edges.EDGE_WALKERS,
but index_repository hardcoded the edge-pass language list to the original
five (python/typescript/tsx/javascript/go) in two places — so those walkers
never ran during real indexing. This test ties the edge-pass language set to
the registry so the two cannot drift again.
"""

from __future__ import annotations

from mememo.chunking.ts_edges import EDGE_WALKERS
from mememo.tools.index_repository import EDGE_PASS_LANGUAGES


def test_edge_pass_covers_every_walker() -> None:
    missing = set(EDGE_WALKERS) - set(EDGE_PASS_LANGUAGES)
    assert not missing, f"edge pass would skip walker languages: {sorted(missing)}"


def test_edge_pass_includes_python() -> None:
    # Python edges come from the AST chunker, not EDGE_WALKERS, so it must be
    # listed explicitly.
    assert "python" in EDGE_PASS_LANGUAGES


def test_edge_pass_includes_newer_languages() -> None:
    # The exact languages whose edges were dead before the fix.
    assert {"rust", "java", "c", "cpp", "csharp"} <= EDGE_PASS_LANGUAGES
