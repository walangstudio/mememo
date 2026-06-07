"""Hybrid lexical+vector retrieval: fusion helpers + FTS reorder."""

from __future__ import annotations

from mememo.core.hybrid import fts_match, fts_terms, reciprocal_rank_fusion


def test_fts_terms_drops_stopwords_punct_and_dedupes() -> None:
    terms = fts_terms("How is the ChatGipite business-idea validation, validation?")
    assert "chatgipite" in terms and "business" in terms and "validation" in terms
    assert "the" not in terms and "is" not in terms  # stopwords
    assert terms.count("validation") == 1  # deduped
    assert "a" not in terms  # 1-char dropped


def test_fts_match_quotes_each_term_and_ors() -> None:
    # Each term quoted as a literal phrase so query operators/punctuation can
    # never produce an FTS5 syntax error.
    assert fts_match('portable identity AND "x"') == '"portable" OR "identity"'
    assert fts_match("") == ""
    assert fts_match("?? -- !!") == ""  # nothing usable


def test_rrf_rewards_agreement_and_top_rank() -> None:
    vec = ["a", "b", "c", "d"]
    lex = ["c", "a", "z"]
    scores = reciprocal_rank_fusion([vec, lex])
    # 'a' (1st vec, 2nd lex) and 'c' (3rd vec, 1st lex) appear in both → outrank
    # 'b'/'d' that only one ranker saw.
    assert scores["a"] > scores["b"]
    assert scores["c"] > scores["d"]
    # 'z' only in lexical, deep nowhere-else → lowest of the lexical hits.
    assert scores["z"] < scores["a"]


def test_rrf_k_damps_deep_ranks() -> None:
    ranking = ["x", "y"]
    high = reciprocal_rank_fusion([ranking], k=1)
    low = reciprocal_rank_fusion([ranking], k=1000)
    # Larger k flattens the gap between rank 1 and rank 2.
    assert (high["x"] - high["y"]) > (low["x"] - low["y"])
