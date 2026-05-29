"""Markdown memory importer — one memory per .md file with YAML frontmatter.

Walk a directory tree of markdown files, parse YAML frontmatter, and create
one memory per file via MemoryManager.create_memory.  URL REFERENCES edges
from the body are persisted via resolve_edges + insert_relations.

Idempotency: a file is skipped when a memory with the same file_path AND
content checksum already exists in the store (content-addressed skip).
Re-importing an unchanged file is always a no-op.  Editing a file creates a
new memory (old one is NOT stale-marked here — that belongs to detect_changes).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ..types.memory import (
    CreateMemoryParams,
    MemoryContentType,
    MemoryRelationships,
)
from ..utils.hashing import calculate_checksum

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# Map YAML frontmatter ``type`` values to MemoryContentType.
_TYPE_MAP: dict[str, MemoryContentType] = {
    "decision": "decision",
    "project": "context",
    "reference": "relationship",
    "user": "context",
    "feedback": "context",
}

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

# Wikilink: [[target]] or [[target|alias]]
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


# Slug from a relative path: strip extension, replace separators with dots.
def _path_slug(rel_path: str) -> str:
    p = Path(rel_path)
    parts = [re.sub(r"[^\w-]", "_", part) for part in p.with_suffix("").parts]
    return ".".join(parts) or "memory"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (meta_dict, body) from a markdown string.

    If no ``--- ... ---`` block is present, returns ({}, text).
    yaml.safe_load is imported lazily to keep module-top imports light.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    import yaml  # lazy — not a module-top import

    try:
        meta = yaml.safe_load(m.group(1)) or {}
        if not isinstance(meta, dict):
            meta = {}
    except Exception:
        meta = {}
    body = text[m.end() :]
    return meta, body


def _map_type(meta: dict) -> MemoryContentType:
    raw = meta.get("type") or meta.get("metadata", {}).get("type") or ""
    if isinstance(raw, str):
        raw = raw.strip().lower()
    return _TYPE_MAP.get(raw, "context")


def _extract_wikilink_edges(body: str) -> list[str]:
    """Return slug targets from [[wikilink]] refs in body."""
    targets: list[str] = []
    seen: set[str] = set()
    for m in _WIKILINK_RE.finditer(body):
        target = m.group(1).strip()
        slug = re.sub(r"[^\w-]", "_", target.lower())
        if slug and slug not in seen:
            seen.add(slug)
            targets.append(slug)
    return targets


async def import_markdown_dir(
    path: str | Path,
    memory_manager: MemoryManager,
    repo: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Import all .md files under ``path`` as memories.

    Args:
        path: Root directory to walk recursively for ``*.md`` files.
        memory_manager: Injected MemoryManager (owns storage + embedding).
        repo: Optional repo root path.  When given, git context is detected
            from that path so the memory is scoped to that code repo's id.
            Defaults to None -> memories are stamped with GLOBAL_REPO_ID.
        dry_run: When True, parse and check but do not write anything.

    Returns:
        dict with keys: imported (int), skipped (int), errors (int),
        details (list[dict]).
    """
    root = Path(path).resolve()
    if not root.is_dir():
        raise ValueError(f"import_markdown_dir: not a directory: {root}")

    cwd = str(repo) if repo else None

    # Pre-fetch checksums already in the store so skip-check is O(1) per file.
    existing = _load_existing_checksums(memory_manager.storage_manager)

    imported = 0
    skipped = 0
    errors = 0
    details: list[dict] = []

    md_files = sorted(root.rglob("*.md"))
    for md_path in md_files:
        rel = str(md_path.relative_to(root))
        try:
            text = md_path.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(text)
            content_type = _map_type(meta)

            # Body is the memory content; strip leading/trailing whitespace.
            content_text = body.strip()
            if not content_text:
                skipped += 1
                details.append({"file": rel, "status": "skipped", "reason": "empty body"})
                continue

            checksum = calculate_checksum(content_text)

            # Idempotent: skip if same file_path + checksum already stored.
            if (rel, checksum) in existing:
                skipped += 1
                details.append({"file": rel, "status": "skipped", "reason": "unchanged"})
                continue

            source_type = meta.get("type", "")
            tags: list[str] = list(meta.get("tags", []) or [])
            if source_type:
                tags.append(f"source_type:{source_type}")

            if dry_run:
                imported += 1
                details.append({"file": rel, "status": "dry_run", "type": content_type})
                continue

            params = CreateMemoryParams(
                content=content_text,
                type=content_type,
                file_path=rel,
                language="markdown",
                tags=tags or None,
                relationships=MemoryRelationships(),
            )
            memory = await memory_manager.create_memory(params, cwd=cwd)

            # Persist edges: URL REFERENCES from chunker + wikilink REFERENCES.
            _emit_edges(
                md_path=md_path,
                rel=rel,
                body=content_text,
                memory_id=memory.id,
                memory_manager=memory_manager,
                repo_id=memory.repo.id,
                branch=memory.branch.name,
                commit_sha=memory.metadata.created_at_sha or ("0" * 40),
            )

            imported += 1
            details.append(
                {"file": rel, "status": "imported", "memory_id": memory.id, "type": content_type}
            )

        except Exception as exc:
            logger.warning("import_markdown_dir: error on %s: %s", rel, exc)
            errors += 1
            details.append({"file": rel, "status": "error", "reason": str(exc)})

    return {"imported": imported, "skipped": skipped, "errors": errors, "details": details}


def _load_existing_checksums(storage_manager) -> set[tuple[str, str]]:
    """Return a set of (file_path, checksum) pairs already in the store."""
    cursor = storage_manager.conn.cursor()
    rows = cursor.execute(
        "SELECT file_path, checksum FROM memories WHERE file_path IS NOT NULL"
    ).fetchall()
    return {(row[0], row[1]) for row in rows}


def _emit_edges(
    md_path: Path,
    rel: str,
    body: str,
    memory_id: str,
    memory_manager: MemoryManager,
    repo_id: str,
    branch: str,
    commit_sha: str,
) -> None:
    """Extract and persist REFERENCES edges (URLs + wikilinks) for one file.

    Uses MarkdownChunker.chunk_with_edges for URL edges; wikilinks are handled
    separately via _extract_wikilink_edges.  Edges are resolved via a minimal
    one-entry symbol table keyed on the file's path slug so resolve_edges can
    find the source memory.
    """
    from ..chunking.base_chunker import RawEdge
    from ..chunking.markdown_chunker import _scan_urls
    from ..core.symbol_resolver import SymbolEntry, resolve_edges

    slug = _path_slug(rel)
    source_entry = SymbolEntry(memory_id=memory_id, qualname=slug)
    symbols = [source_entry]

    raw_edges: list[RawEdge] = []

    # Scan the whole body for URLs — the chunker only scans per heading section
    # so preamble URLs (no-heading files) are missed by chunk_with_edges.
    for url in _scan_urls(body.splitlines()):
        raw_edges.append(RawEdge(slug, url, "REFERENCES", "INFERRED"))

    # Wikilink REFERENCES.
    for wl_target in _extract_wikilink_edges(body):
        raw_edges.append(RawEdge(slug, wl_target, "REFERENCES", "INFERRED"))

    if not raw_edges:
        return

    # resolve_edges skips sources not in the symbol table; our slug IS registered
    # above.  URL/wikilink targets resolve as AMBIGUOUS with target_symbol set.
    relations = resolve_edges(
        raw_edges,
        repo_id=repo_id,
        branch=branch,
        commit_sha=commit_sha,
        symbols=symbols,
    )
    if relations:
        inserted = memory_manager.storage_manager.insert_relations(relations)
        logger.debug("_emit_edges: %s -> %d relations inserted", rel, inserted)
