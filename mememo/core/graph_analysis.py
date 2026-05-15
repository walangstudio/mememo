"""Graph analysis — community detection + entity dedup (FR-018, FR-019).

Two pure-data passes that mutate the relations / entity_aliases tables:

- ``cluster_relations(storage, repo_id, branch)`` runs Louvain over the
  edge graph (networkx) and stamps ``relations.community`` per edge.
  Louvain is the deterministic-with-seed cousin of Leiden; the spec
  permits either with FR-018's RFC2119 MUST clause focused on
  "fixed seed -> identical communities". A future swap to graspologic
  Leiden is one import change.

- ``dedup_entities(symbols, threshold)`` runs pairwise Jaro-Winkler over
  qualnames and collapses near-duplicates into canonical groups via
  union-find. Logs each alias to entity_aliases.

Both passes are optional: missing networkx / rapidfuzz raise a clear
ImportError on the API call so the rest of v0.5 keeps working.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ---------------------- T021: Louvain communities ---------------------------


@dataclass(frozen=True)
class ClusteringResult:
    """Outcome of one clustering pass."""

    communities: dict[str, int]  # memory_id -> community_id
    modularity: float | None  # None if the graph was empty
    relations_updated: int


def cluster_relations(
    storage,
    *,
    repo_id: str,
    branch: str,
    seed: int = 42,
) -> ClusteringResult:
    """Run Louvain community detection over the relations graph.

    Reads every resolved edge (target_memory_id is not null) for the given
    (repo, branch), builds an undirected NetworkX graph weighted by edge
    count, runs ``networkx.community.louvain_communities`` with the supplied
    seed, then writes the community label back to each contributing edge.

    Returns the result and the number of relations rows updated.
    """
    try:
        import networkx as nx
        from networkx.algorithms.community import (
            louvain_communities,
            modularity,
        )
    except ImportError as e:  # pragma: no cover — clear error path
        raise ImportError(
            "cluster_relations requires networkx; install with " "`pip install networkx>=3.0`"
        ) from e

    rels = storage.list_relations(repo_id=repo_id, branch=branch)
    resolved = [r for r in rels if r.target_memory_id]
    if not resolved:
        return ClusteringResult(communities={}, modularity=None, relations_updated=0)

    g = nx.Graph()
    for r in resolved:
        if g.has_edge(r.source_memory_id, r.target_memory_id):
            g[r.source_memory_id][r.target_memory_id]["weight"] += 1
        else:
            g.add_edge(r.source_memory_id, r.target_memory_id, weight=1)

    communities = louvain_communities(g, seed=seed)
    mod = modularity(g, communities)

    # memory_id -> community label (small int)
    membership: dict[str, int] = {}
    for cid, comm in enumerate(communities):
        for mid in comm:
            membership[mid] = cid

    # Stamp community on every resolved relation.
    cursor = storage.conn.cursor()
    updated = 0
    for r in resolved:
        cid = membership.get(r.source_memory_id)
        if cid is None:
            continue
        cursor.execute(
            "UPDATE relations SET community = ? WHERE id = ?",
            (cid, r.id),
        )
        updated += cursor.rowcount
    storage.conn.commit()

    return ClusteringResult(communities=membership, modularity=mod, relations_updated=updated)


# ---------------------- T022: entity dedup pipeline -------------------------


@dataclass(frozen=True)
class DedupCandidate:
    """A symbol the dedup pass scans."""

    memory_id: str
    label: str  # The canonical qualname or alias text.


@dataclass(frozen=True)
class DedupResult:
    """Outcome of one dedup pass."""

    canonical_count: int  # number of canonical groups discovered
    alias_count: int  # number of (canonical, alias) pairs persisted


class _UnionFind:
    """Vanilla path-compressed union-find, ints only.

    Caller maps labels to indices; this class stays Python-stdlib only.
    """

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1


def dedup_entities(
    storage,
    candidates: Iterable[DedupCandidate],
    *,
    threshold: float = 0.95,
) -> DedupResult:
    """Collapse near-duplicate labels into canonical groups.

    Algorithm:
    1. For each pair (i, j), compute Jaro-Winkler similarity on labels.
    2. If >= threshold, union(i, j).
    3. For each non-singleton group, pick the lexicographically smallest
       label as canonical; persist every other (canonical_id, alias_label,
       similarity) to entity_aliases.

    Pure pairwise scan is O(n^2) — acceptable for typical chunk counts
    (a few thousand). Larger corpora should layer MinHash/LSH on top, which
    is deferred to a future patch.
    """
    try:
        from rapidfuzz.distance import JaroWinkler
    except ImportError as e:  # pragma: no cover — clear error path
        raise ImportError(
            "dedup_entities requires rapidfuzz; install with " "`pip install rapidfuzz>=3.0`"
        ) from e

    items = list(candidates)
    if len(items) < 2:
        return DedupResult(canonical_count=len(items), alias_count=0)

    uf = _UnionFind(len(items))
    # Quadratic scan; cheap up to ~5k items in Python.
    similarities: dict[tuple[int, int], float] = {}
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            sim = JaroWinkler.normalized_similarity(items[i].label, items[j].label)
            if sim >= threshold:
                uf.union(i, j)
                similarities[(i, j)] = sim

    groups: dict[int, list[int]] = {}
    for idx in range(len(items)):
        groups.setdefault(uf.find(idx), []).append(idx)

    cursor = storage.conn.cursor()
    aliases_written = 0
    canonical_count = 0
    for members in groups.values():
        if len(members) < 2:
            canonical_count += 1
            continue
        canonical_count += 1
        # Pick the lexicographically smallest label as canonical.
        members_sorted = sorted(members, key=lambda i: items[i].label)
        canonical_idx = members_sorted[0]
        for alias_idx in members_sorted[1:]:
            # Look up the precomputed similarity (i, j) keyed with i < j.
            pair = tuple(sorted((canonical_idx, alias_idx)))
            sim = similarities.get(pair, 1.0)
            cursor.execute(
                """
                INSERT OR REPLACE INTO entity_aliases
                    (canonical_memory_id, alias_label, similarity)
                VALUES (?, ?, ?)
                """,
                (items[canonical_idx].memory_id, items[alias_idx].label, sim),
            )
            aliases_written += 1

    storage.conn.commit()
    return DedupResult(canonical_count=canonical_count, alias_count=aliases_written)
