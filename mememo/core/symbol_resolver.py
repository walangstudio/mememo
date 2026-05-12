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
from dataclasses import dataclass
from typing import Iterable
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


def _resolve_one(
    label: str, table: dict[str, list[SymbolEntry]], fuzzy_threshold: float = 0.95
) -> tuple[SymbolEntry | None, EdgeConfidence]:
    # 1. Exact match.
    exact = table.get(label)
    if exact and len(exact) == 1:
        return exact[0], "EXTRACTED"
    if exact and len(exact) > 1:
        return None, "AMBIGUOUS"

    # 2. Suffix match — `bar` -> `foo.bar` when there's exactly one candidate.
    suffix_candidates = [
        entries[0]
        for qual, entries in table.items()
        if len(entries) == 1
        and (qual == label or qual.endswith(f".{label}"))
    ]
    if len(suffix_candidates) == 1:
        return suffix_candidates[0], "EXTRACTED"
    if len(suffix_candidates) > 1:
        return None, "AMBIGUOUS"

    # 3. Fuzzy match — Jaro-Winkler >= threshold, single candidate above it.
    if _HAS_RAPIDFUZZ:
        scored = [
            (entries[0], JaroWinkler.normalized_similarity(label, qual))
            for qual, entries in table.items()
            if len(entries) == 1
        ]
        above = [(e, s) for e, s in scored if s >= fuzzy_threshold]
        if len(above) == 1:
            return above[0][0], "INFERRED"
        if len(above) > 1:
            return None, "AMBIGUOUS"

    return None, "AMBIGUOUS"


def resolve_edges(
    raw_edges: list[RawEdge],
    *,
    repo_id: str,
    branch: str,
    commit_sha: str,
    symbols: Iterable[SymbolEntry],
    fuzzy_threshold: float = 0.95,
) -> list[Relation]:
    """Resolve a batch of raw edges into persistable Relation rows.

    Edges whose ``source_qualname`` is unknown to the symbol table are
    skipped silently — they correspond to module-level emit at the import
    site where no chunk-memory exists. The CALLER decides whether to widen
    by creating module-level chunks.
    """
    table = build_symbol_table(symbols)
    out: list[Relation] = []
    for raw in raw_edges:
        # Find the source chunk-memory by qualname (single owner per (repo,branch)).
        source_owners = table.get(raw.source_qualname, [])
        if not source_owners:
            # Try suffix match for module-level edges
            suffix = [
                e[0]
                for q, e in table.items()
                if len(e) == 1 and (q == raw.source_qualname or q.endswith(f".{raw.source_qualname}"))
            ]
            if len(suffix) != 1:
                logger.debug(
                    "resolve_edges: skipping edge with no source owner: %s -> %s",
                    raw.source_qualname, raw.target_label,
                )
                continue
            source = suffix[0]
        elif len(source_owners) == 1:
            source = source_owners[0]
        else:
            logger.debug(
                "resolve_edges: ambiguous source %s (%d owners)",
                raw.source_qualname, len(source_owners),
            )
            continue

        target, confidence = _resolve_one(raw.target_label, table, fuzzy_threshold)
        # Demote to AMBIGUOUS-style when we can't pin the target but keep the symbol.
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
