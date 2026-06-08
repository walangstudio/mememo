"""Skill curation: keep the distilled-skill library consolidated.

As autonomous distillation (Phase A) adds skills over time, near-duplicate skills
accumulate — the same technique re-learned under a different name. Hermes' v0.12+
loop handles this with a periodic curator that grades and *consolidates* the skill
library; this module is the consolidation primitive: it clusters skills by semantic
similarity so a curator pass (tools/curate_skills.py) can merge them.

Pure logic. The Embedder L2-normalizes its output, so cosine similarity is just the
dot product — no separate normalization here. The embedder and any deletion are the
caller's job.
"""

from __future__ import annotations

import numpy as np

# Default cosine-similarity threshold above which two skills are "near-duplicates".
# Shared by curate_skills (clustering) and the manage_skill create-time nudge so a
# duplicate flagged on create is the same one the curator would later cluster.
DEFAULT_DUP_THRESHOLD = 0.86


def cluster_duplicates(vectors: np.ndarray, threshold: float) -> list[list[int]]:
    """Group skill indices whose pairwise cosine similarity is >= threshold.

    Union-find over the upper triangle of the similarity matrix: any two skills
    above threshold join the same cluster, so a chain A~B~C clusters together even
    if A and C are just under threshold. Only clusters with >= 2 members are
    returned (a singleton has no duplicate), each as a sorted index list, ordered
    by smallest member for a stable result.
    """
    n = len(vectors)
    if n < 2:
        return []

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path halving
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    sims = vectors @ vectors.T
    for i in range(n):
        for j in range(i + 1, n):
            if sims[i][j] >= threshold:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    clusters = [sorted(g) for g in groups.values() if len(g) >= 2]
    clusters.sort(key=lambda g: g[0])
    return clusters


def nearest(
    target: np.ndarray, candidates: np.ndarray, threshold: float
) -> tuple[int, float] | None:
    """(index, score) of the candidate most similar to ``target`` at/above threshold.

    ``candidates`` is an (n, d) matrix of the other skills' vectors. Returns None
    when there are no candidates or none clear the threshold. Used by the create-time
    dedup nudge to point at the single closest existing skill.
    """
    if len(candidates) == 0:
        return None
    sims = candidates @ target
    idx = int(np.argmax(sims))
    score = float(sims[idx])
    return (idx, score) if score >= threshold else None
