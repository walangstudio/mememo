# ADR 0001 — Multi-modal support: Markdown docs and links

Status: **Proposed** (for review; no implementation yet)
Date: 2026-05-24

## Context

mememo graphs code (9 languages, typed edges) and stores conversational memories,
but treats `.md` files as unstructured text and ignores URLs entirely. Tools like
Graphify graph docs and references alongside code so an agent can traverse from a
design note to the code it describes. We want the same for the common case —
Markdown and links — without breaking mememo's invariants:

- fully local; works with no API key (passthrough mode)
- no silent auto-expiry (manual cleanup only)
- git/commit-aware (edges and memories versioned by SHA)
- secrets detection/sanitization applies to all ingested text

Out of scope: images/diagrams. The embedder is text-only (sentence-transformers);
true multi-modal embeddings would require a vision model and break local-first.
Deferred to a future opt-in local-caption backend.

## Decision

Two slices.

### P1 — Markdown chunker + `DOCUMENTS` edge

- New `mememo/chunking/markdown_chunker.py` implementing `BaseChunker`. **Pure-Python
  heading parser**, not tree-sitter: split on `#`..`######` into heading-scoped
  chunks with a parent/child hierarchy (heading-level stack); capture fenced code
  blocks (with their info-string language) and backtick-quoted symbol mentions.
- New edge type `DOCUMENTS` (doc section → code symbol). Doc-chunk qualname is
  `{relative_file_path}.{heading_slug}` to avoid cross-file `## Overview` collisions.
- Symbol mentions resolve through the existing `symbol_resolver`, which is
  edge-type-agnostic: unresolved mentions degrade to `confidence=AMBIGUOUS` with a
  null target and are skipped by graph traversal — benign.

Why pure-Python over `tree-sitter-markdown`: heading structure is ~95% of the value
with zero new dependency and a smaller test surface. The only material gain from the
grammar is richer intra-section structure; the code-fence language hint is a one-line
regex on the fence info string. tree-sitter-markdown stays a clean drop-in upgrade if
we later need it.

`Chunk` reuse: add `"heading"` to the `ChunkType` literal; store the heading text in
`function_name` and the parent heading in `parent_class` (mirrors how `impl` blocks
repurpose `class_name`). `class_name` stays `None` for doc chunks.

### P2 — Links + `REFERENCES` edge + `reference` memory type

- Stdlib-regex URL extraction (require an `https?://` scheme to avoid matching file
  paths) from doc text, code comments/docstrings, and captured conversation.
- Each unique URL → one `reference` memory (searchable) **and** a `REFERENCES` edge
  (traversable) from the chunk/memory that mentions it. Dedup on a normalized URL
  (strip fragment, canonicalize trailing slash) via the existing checksum dedup; one
  URL memory regardless of mention count, many edges pointing at it.
- `reference` joins `PERSISTENT_MEMORY_TYPES` (URLs are not staled by code changes).
- Capture-side: after store, extract URLs and relate them to the captured memory.
  Passthrough mode extends the prompt to ask the host model to `store_memory` URLs —
  best-effort, no API key required.

## The taxonomy change (blast radius)

`EdgeType` is duplicated in **three** places that must stay in sync:

| Location | What enforces it |
|---|---|
| `mememo/chunking/base_chunker.py` | `EdgeType` Literal (type-checker) |
| `mememo/types/memory.py` | `RelationType` Literal (Pydantic validation) |
| `mememo/core/storage_manager.py` | SQLite `CHECK (type IN (...))` on the `relations` table |

Verified **no** change needed in: `cypher_parser` (passes `rel_type` through as a SQL
param, no validation), `graph_analysis` (operates on memory-id nodes, never branches
on edge type), `graph_impact` (runtime-filters on `RelationType`, only the annotation
updates), and `web/` (no edge-type string references).

**Migration hazard:** SQLite cannot `ALTER` a `CHECK` constraint, so adding edge
values on an existing database requires recreating the `relations` table. Decision:
**drop the DB-level `CHECK` and rely on the Pydantic `RelationType` Literal** — every
edge already passes through Pydantic before insert, so the DB check is redundant. The
migration is one idempotent block (create-new → copy → drop → rename, inside a
transaction with `PRAGMA foreign_keys=OFF`), gated on a `schema_meta` version marker.

## Consequences

- `MATCH (d)-[:DOCUMENTS]->(c)` and `(c)-[:REFERENCES]->(url)` become queryable via
  the Cypher subset and `graph_impact`.
- One new chunker, two new edge types, one new memory type, one schema migration.
- Doc→code edges are lower-precision than code→code (correctly `INFERRED`/`AMBIGUOUS`).
- Removing the DB `CHECK` shifts edge-type enforcement entirely to the app layer —
  acceptable given universal Pydantic validation, and it makes all future edge-type
  additions migration-free.

## Alternatives considered

- **Reuse `USES` for doc/link edges** — rejected: corrupts code-internal `USES`
  semantics and makes `graph_impact` results uninterpretable.
- **URLs as edge targets only (no memory)** — rejected: not semantically searchable.
- **URLs as memory only (no edge)** — rejected: not graph-traversable. We do both.
- **tree-sitter-markdown for P1** — deferred: dependency + test cost without
  proportional value; keep as an upgrade path.

## Phasing, tests, risks

**P1 files:** `base_chunker.py` (+`heading`, +`DOCUMENTS`), `types/memory.py`
(+`DOCUMENTS`), `storage_manager.py` (CHECK→Pydantic migration + `schema_meta`),
`language_detector.py` (markdown category → `markdown` chunker), `factory.py`
(markdown branch), new `markdown_chunker.py`.
Tests mirror `tests/test_edge_walkers_*.py`: heading nesting, empty sections, fenced
code, backtick symbols → `DOCUMENTS`; one round-trip test chunk→resolve→insert→Cypher.

**P2 files:** `base_chunker.py` (+`REFERENCES`), `types/memory.py` (+`reference`,
+`REFERENCES`, persistent-types), `markdown_chunker.py` (URL edges), `tools/capture.py`
(`_extract_urls`, `reference` in `_EXTRACT_TYPES`, passthrough prompt). Tests: URL-regex
unit (rejects bare paths), markdown link → `REFERENCES`, capture→reference-memory+edge.

**Top risks:**
1. `relations` table migration on existing DBs — must be transactional and idempotent.
2. Heading-slug qualname collisions — mitigated by full-path prefix.
3. Secrets in URL query strings — sanitize captured text **before** URL extraction;
   confirm the call order at the capture site.
4. Passthrough URL extraction is best-effort — accepted (consistent with passthrough).

## Open questions

- Should code-comment URL extraction (P2) scan only docstrings/comment nodes, or all
  string literals? Recommendation: comments/docstrings only, to avoid false positives
  from URL-shaped data.
- Sequence the two P-slices as separate PRs (one migration each, or share P1's)?
