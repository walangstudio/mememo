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


def test_default_index_globs_cover_every_walker_language() -> None:
    # The same drift class as above, but for the DEFAULT file patterns: the old
    # hard-coded ["**/*.py","**/*.ts","**/*.js","**/*.go","**/*.rs"] matched none
    # of Java/C#/Kotlin/Ruby/PHP/Swift/Scala, so a default index of those repos
    # found zero files. Tie the default globs to the language map.
    from mememo.chunking.language_detector import LANGUAGE_MAP, get_index_globs

    globs = set(get_index_globs())
    covered_langs = {LANGUAGE_MAP[ext] for ext in LANGUAGE_MAP if f"**/*{ext}" in globs}
    detected_langs = set(LANGUAGE_MAP.values())
    for lang in EDGE_WALKERS:
        if lang not in detected_langs:
            continue  # pure walker alias (e.g. 'tsx' -> detected as 'typescript')
        assert lang in covered_langs, f"default index globs skip walker language: {lang}"
    # Markdown stays out (import-md owns it); a few specific extensions present.
    assert "**/*.md" not in globs
    assert {"**/*.java", "**/*.kt", "**/*.rb", "**/*.php", "**/*.swift", "**/*.scala"} <= globs
    # Chunker-less languages (text fallback only) are excluded so a default index
    # doesn't text-blob them or log per-file "unsupported" warnings.
    assert "**/*.svelte" not in globs
    assert "**/*.vue" not in globs


def test_index_params_default_uses_all_languages() -> None:
    from mememo.chunking.language_detector import get_index_globs
    from mememo.tools.schemas import IndexRepositoryParams, SyncCommitsParams

    expected = get_index_globs()
    assert IndexRepositoryParams(repo_path="x").file_patterns == expected
    assert SyncCommitsParams(repo_path="x").file_patterns == expected
