"""Symbol resolver — turn RawEdges into persistable Relations (FR-015).

Build a ``(repo, branch, qualname) -> memory_id`` table from the memories
already on disk, then resolve every raw edge's ``target_label`` against it.

Resolution rules:
- Exact match on the qualname -> confidence=EXTRACTED
- Suffix match (target_label is the tail of exactly one known qualname) ->
  EXTRACTED (handles `bar()` calls that resolve to `foo.bar` when only one
  candidate exists)
- Single Jaro-Winkler >= 0.95 candidate -> INFERRED
- Zero or multiple candidates -> AMBIGUOUS, target_memory_id stays None

Pure: no SQLite mutation, no FAISS. Caller persists the returned Relations.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import uuid4

from ..chunking.base_chunker import EdgeConfidence, RawEdge
from ..types import Relation

logger = logging.getLogger(__name__)

# Optional fuzzy-match dep. The code falls back to suffix-only resolution
# when rapidfuzz isn't installed so v0.5 batch 1 doesn't require it.
try:
    from rapidfuzz.distance import JaroWinkler  # type: ignore

    _HAS_RAPIDFUZZ = True
except ImportError:  # pragma: no cover
    _HAS_RAPIDFUZZ = False


# A trimmed view of what the resolver needs about a chunk-memory.
@dataclass(frozen=True)
class SymbolEntry:
    memory_id: str
    qualname: str  # e.g. "mememo.core.storage_manager.StorageManager.save_memory"


def build_symbol_table(entries: Iterable[SymbolEntry]) -> dict[str, list[SymbolEntry]]:
    """Group entries by qualname. A qualname may have multiple owners across
    branches; callers prefilter to a single (repo, branch) first.
    """
    table: dict[str, list[SymbolEntry]] = {}
    for entry in entries:
        table.setdefault(entry.qualname, []).append(entry)
    return table


def _build_tail_index(
    table: dict[str, list[SymbolEntry]],
) -> dict[str, list[SymbolEntry]]:
    """``{ tail_name: [entries] }`` for every uniquely-owned qualname.

    Built once per ``resolve_edges`` call to give suffix lookup O(1) cost.
    Includes the full qualname as its own tail so exact matches and pure
    suffix matches share one lookup path.
    """
    out: dict[str, list[SymbolEntry]] = {}
    for qual, entries in table.items():
        if len(entries) != 1:
            continue
        out.setdefault(qual, []).append(entries[0])
        tail = qual.rsplit(".", 1)[-1] if "." in qual else qual
        if tail != qual:
            out.setdefault(tail, []).append(entries[0])
    return out


# Receivers that denote "the current instance/class" across the languages we
# walk: this (TS/JS/Java/C++/C#/Kotlin/Scala), self (Python/Rust/Swift/Ruby),
# $this (PHP), Self (Rust associated fns), cls (Python classmethods).
_SELF_RECEIVERS = frozenset({"this", "self", "Self", "cls"})


def _rebind_self_receiver(target_label: str, source_qualname: str) -> str | None:
    """Rebind a single-hop ``self``/``this`` call to the source's own class.

    Tree-sitter walkers emit an intra-class call like ``this.helper`` /
    ``self.helper`` / ``$this.helper`` / ``Self::helper`` with the receiver kept
    verbatim, which never matches a qualname. The receiver means "this class",
    so rebind it to ``<source class>.helper`` (the source method's qualname minus
    its own trailing name). Returns None when the label isn't a single-hop
    self/this call, or the source has no class prefix.
    """
    for sep in (".", "::"):
        head, found, rest = target_label.partition(sep)
        if not found or not rest or "." in rest or "::" in rest:
            continue  # not a separator hit, or a multi-hop chain (this.a.b)
        if head in _SELF_RECEIVERS or head == "$this":  # PHP uses $this
            class_qual = source_qualname.rsplit(".", 1)[0] if "." in source_qualname else ""
            return f"{class_qual}.{rest}" if class_qual else None
    return None


def _resolve_one(
    label: str,
    table: dict[str, list[SymbolEntry]],
    tail_index: dict[str, list[SymbolEntry]],
    fuzzy_qualnames: list[str] | None = None,
    fuzzy_threshold: float = 0.95,
) -> tuple[SymbolEntry | None, EdgeConfidence]:
    # 1. Exact match.
    exact = table.get(label)
    if exact and len(exact) == 1:
        return exact[0], "EXTRACTED"
    if exact and len(exact) > 1:
        return None, "AMBIGUOUS"

    # 2. Suffix match via the precomputed tail index. O(1) lookup.
    suffix_candidates = tail_index.get(label)
    if suffix_candidates:
        if len(suffix_candidates) == 1:
            return suffix_candidates[0], "EXTRACTED"
        return None, "AMBIGUOUS"

    # 3. Fuzzy match — only consult rapidfuzz when an index of unique-owner
    # qualnames is supplied. Callers can disable by passing fuzzy_qualnames=None
    # (the default) when the corpus is too large for an O(N) scan per edge.
    if _HAS_RAPIDFUZZ and fuzzy_qualnames:
        above: list[tuple[SymbolEntry, float]] = []
        for qual in fuzzy_qualnames:
            score = JaroWinkler.normalized_similarity(label, qual)
            if score >= fuzzy_threshold:
                above.append((table[qual][0], score))
                if len(above) > 1:
                    return None, "AMBIGUOUS"
        if len(above) == 1:
            return above[0][0], "INFERRED"

    return None, "AMBIGUOUS"


def resolve_edges(
    raw_edges: list[RawEdge],
    *,
    repo_id: str,
    branch: str,
    commit_sha: str,
    symbols: Iterable[SymbolEntry],
    fuzzy_threshold: float = 0.95,
    fuzzy_max_symbols: int = 2_000,
) -> list[Relation]:
    """Resolve a batch of raw edges into persistable Relation rows.

    Edges whose ``source_qualname`` is unknown to the symbol table are
    skipped silently — they correspond to module-level emit at the import
    site where no chunk-memory exists. The CALLER decides whether to widen
    by creating module-level chunks.

    Performance (FR-016): suffix lookups use a precomputed tail index;
    fuzzy matching is skipped automatically when the symbol set exceeds
    ``fuzzy_max_symbols`` so the resolver stays O(E) on large corpora.
    """
    table = build_symbol_table(symbols)
    tail_index = _build_tail_index(table)
    # Only enable fuzzy when the corpus is small enough that the O(E * S)
    # cost stays inside the budget.
    fuzzy_qualnames: list[str] | None = None
    if _HAS_RAPIDFUZZ:
        unique_quals = [q for q, e in table.items() if len(e) == 1]
        if len(unique_quals) <= fuzzy_max_symbols:
            fuzzy_qualnames = unique_quals

    out: list[Relation] = []
    for raw in raw_edges:
        # Find the source chunk-memory by qualname (single owner per (repo,branch)).
        source_owners = table.get(raw.source_qualname, [])
        if not source_owners:
            suffix = tail_index.get(raw.source_qualname)
            if not suffix or len(suffix) != 1:
                logger.debug(
                    "resolve_edges: skipping edge with no source owner: %s -> %s",
                    raw.source_qualname,
                    raw.target_label,
                )
                continue
            source = suffix[0]
        elif len(source_owners) == 1:
            source = source_owners[0]
        else:
            logger.debug(
                "resolve_edges: ambiguous source %s (%d owners)",
                raw.source_qualname,
                len(source_owners),
            )
            continue

        target, confidence = _resolve_one(
            raw.target_label, table, tail_index, fuzzy_qualnames, fuzzy_threshold
        )
        # Fallback for intra-class calls the walkers leave receiver-qualified
        # (``this.helper`` / ``self.helper`` / ``$this.helper`` / ``Self::new``).
        # Only when the raw label didn't resolve, so resolved edges never change.
        if target is None and raw.edge_type == "CALLS":
            rebound = _rebind_self_receiver(raw.target_label, raw.source_qualname)
            if rebound is not None:
                # Exact match only: the ``self.`` receiver means "this class", so
                # bind solely to the sibling method ``<class>.x`` when it exists.
                # A suffix/fuzzy fallback here would defeat that and bind to an
                # unrelated ``x`` in some other class.
                owners = table.get(rebound)
                if owners and len(owners) == 1:
                    target, confidence = owners[0], "EXTRACTED"
        out.append(
            Relation(
                id=str(uuid4()),
                repo_id=repo_id,
                branch=branch,
                source_memory_id=source.memory_id,
                target_memory_id=target.memory_id if target else None,
                target_symbol=None if target else raw.target_label,
                type=raw.edge_type,
                confidence=confidence,
                created_at_sha=commit_sha,
            )
        )
    return out
