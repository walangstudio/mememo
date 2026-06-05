"""Hybrid lexical+vector retrieval helpers.

Vector search (MiniLM) blurs terse jargon and exact identifiers — a query like
"business idea validation" or a project name like "ChatGipite" can rank the
right memory below an unrelated one. The SQLite FTS5 ``memories_fts`` table
matches those tokens exactly. These pure helpers turn a query into a safe FTS5
MATCH expression and fuse the two ranked lists with Reciprocal Rank Fusion, so
the lexical signal reorders the vector candidates without either ranker's raw
scores having to be comparable.
"""

from __future__ import annotations

import re

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")

# Minimal English stopword set: dropped so the OR-of-terms FTS query isn't
# dominated by filler that matches nearly every memory.
_STOPWORDS = frozenset(
    "a an and are as at be been being but by can did do does for from had has have how "
    "i if in into is it its of on or that the their them then there these they this to "
    "was were what when where which who why will with you your".split()
)


def fts_terms(query: str, max_terms: int = 24) -> list[str]:
    """Distinct, lowercased content words from a query (stopwords/1-char dropped)."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _WORD_RE.findall(query or ""):
        t = m.lower()
        if len(t) < 2 or t in _STOPWORDS or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_terms:
            break
    return out


def fts_match(query: str) -> str:
    """A safe FTS5 MATCH expression: each term quoted as a literal phrase and
    OR-joined, so query punctuation/operators can never raise an FTS5 syntax
    error. Empty string when the query has no usable terms (caller skips FTS)."""
    return " OR ".join(f'"{t}"' for t in fts_terms(query))


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    """Reciprocal Rank Fusion of several best-first id lists.

    Each list contributes ``1/(k + rank)`` (rank 1-based) to an id's score, so a
    high placement in either ranker lifts an item without the rankers sharing a
    score scale. ``k`` damps the influence of deep ranks (the standard 60).
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank + 1)
    return scores
