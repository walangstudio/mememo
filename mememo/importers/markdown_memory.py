"""Markdown memory importer — one memory per heading section (default).

Walk a directory tree of markdown files, parse YAML frontmatter, and create
memories via MemoryManager.create_memory.  By default each markdown
heading-section becomes its own memory (``per_section=True``) so embeddings are
focused and semantic recall is precise; ``per_section=False`` keeps the legacy
one-memory-per-file behaviour.  URL + wikilink REFERENCES edges are persisted
per section via resolve_edges + insert_relations.

Idempotency: a section is skipped when a memory with the same file_path AND
content checksum already exists (content-addressed skip).  Re-importing an
unchanged file is a no-op.  Editing a section creates a new memory (the old one
is NOT stale-marked here — that belongs to detect_changes).
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
    "reference": "reference",
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


class _Unit:
    """One importable unit: a heading section (or a whole file / preamble)."""

    __slots__ = ("content", "qualname", "heading", "line_range")

    def __init__(self, content, qualname, heading, line_range):
        self.content = content
        self.qualname = qualname
        self.heading = heading
        self.line_range = line_range


def _section_body(text: str) -> str:
    """Body of a heading chunk = its text minus the leading heading line.

    A heading with no following lines (no newline) has an empty body.
    """
    nl = text.find("\n")
    return "" if nl == -1 else text[nl + 1 :]


def _file_units(body: str, rel: str, per_section: bool) -> list[_Unit]:
    """Split a file body into importable units.

    per_section=True -> one unit per markdown heading section (+ preamble), each
    holding its own text (subsections excluded). Heading-only sections with no
    body are dropped (a bare heading is recall noise and carries no edges).
    per_section=False -> a single whole-file unit (legacy behaviour).
    """
    if not per_section:
        ct = body.strip()
        return [_Unit(ct, _path_slug(rel), None, None)] if ct else []

    from ..chunking.markdown_chunker import MarkdownChunker
    from ..chunking.python_ast_chunker import file_path_to_module

    chunks = MarkdownChunker().chunk(body, rel)
    module = file_path_to_module(rel)
    units: list[_Unit] = []
    for ch in chunks:
        if ch.chunk_type == "heading":
            section_text = ch.text
            if not _section_body(section_text).strip():
                continue  # heading with no body -> skip
            qual = ch.qualname or f"{module}.{_slug_for(ch.function_name)}"
            units.append(_Unit(section_text, qual, ch.function_name, (ch.start_line, ch.end_line)))
        else:  # preamble "text" chunk
            if not ch.text.strip():
                continue
            qual = ch.qualname or f"{module}.__preamble__"
            units.append(_Unit(ch.text.strip(), qual, None, (ch.start_line, ch.end_line)))
    return units


def _slug_for(text: str | None) -> str:
    return re.sub(r"[^\w-]", "_", (text or "section").lower()) or "section"


async def import_markdown_dir(
    path: str | Path,
    memory_manager: MemoryManager,
    repo: str | None = None,
    dry_run: bool = False,
    allow_secrets: bool = False,
    per_section: bool = True,
) -> dict:
    """Import all .md files under ``path`` as memories.

    Args:
        path: Root directory to walk recursively for ``*.md`` files.
        memory_manager: Injected MemoryManager (owns storage + embedding).
        repo: Optional repo root path.  When given, git context is detected
            from that path so the memory is scoped to that code repo's id.
            Defaults to None -> memories are stamped with GLOBAL_REPO_ID.
        dry_run: When True, parse and check but do not write anything.
        allow_secrets: Bypass secret detection. Local markdown memory often
            contains placeholder credentials (e.g. ``a postgres:// DSN``)
            that the scanner rejects; set True to import trusted files anyway.
        per_section: When True (default), create one memory per heading section
            for precise recall. When False, one memory per whole file (legacy).

    Returns:
        dict with keys: imported (int), skipped (int), errors (int),
        details (list[dict]).
    """
    root = Path(path).resolve()
    if not root.is_dir():
        raise ValueError(f"import_markdown_dir: not a directory: {root}")

    cwd = str(repo) if repo else None

    # Pre-fetch checksums already in the store so skip-check is O(1) per section.
    existing = _load_existing_checksums(memory_manager.storage_manager)

    imported = 0
    skipped = 0
    errors = 0
    details: list[dict] = []

    for md_path in sorted(root.rglob("*.md")):
        rel = str(md_path.relative_to(root))
        try:
            text = md_path.read_text(encoding="utf-8-sig")
            meta, body = _parse_frontmatter(text)
            content_type = _map_type(meta)

            source_type = meta.get("type") or meta.get("metadata", {}).get("type") or ""
            tags: list[str] = list(meta.get("tags", []) or [])
            if source_type:
                tags.append(f"source_type:{source_type}")

            units = _file_units(body, rel, per_section)
            if not units:
                skipped += 1
                details.append({"file": rel, "status": "skipped", "reason": "empty body"})
                continue

            qual_to_id: dict[str, str] = {}
            commit_sha = "0" * 40
            repo_id = branch = None

            for unit in units:
                checksum = calculate_checksum(unit.content)
                if (rel, checksum) in existing:
                    skipped += 1
                    details.append(
                        {
                            "file": rel,
                            "heading": unit.heading,
                            "status": "skipped",
                            "reason": "unchanged",
                        }
                    )
                    continue
                existing.add((rel, checksum))

                if dry_run:
                    imported += 1
                    details.append(
                        {
                            "file": rel,
                            "heading": unit.heading,
                            "status": "dry_run",
                            "type": content_type,
                        }
                    )
                    continue

                params = CreateMemoryParams(
                    content=unit.content,
                    type=content_type,
                    file_path=rel,
                    line_range=unit.line_range,
                    function_name=unit.heading,
                    language="markdown",
                    tags=tags or None,
                    relationships=MemoryRelationships(),
                )
                memory = await memory_manager.create_memory(
                    params,
                    cwd=cwd,
                    skip_secret_scan=allow_secrets,
                    force_global=repo is None,
                )
                qual_to_id[unit.qualname] = memory.id
                repo_id = memory.repo.id
                branch = memory.branch.name
                commit_sha = memory.metadata.created_at_sha or commit_sha

                imported += 1
                details.append(
                    {
                        "file": rel,
                        "heading": unit.heading,
                        "status": "imported",
                        "memory_id": memory.id,
                        "type": content_type,
                    }
                )

            # Per-section edges: each section's URLs/wikilinks attach to that
            # section's memory (resolve by qualname against qual_to_id).
            if not dry_run and qual_to_id and repo_id is not None:
                _emit_section_edges(
                    units=units,
                    qual_to_id=qual_to_id,
                    memory_manager=memory_manager,
                    repo_id=repo_id,
                    branch=branch,
                    commit_sha=commit_sha,
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


def _emit_section_edges(
    units: list[_Unit],
    qual_to_id: dict[str, str],
    memory_manager: MemoryManager,
    repo_id: str,
    branch: str,
    commit_sha: str,
) -> None:
    """Persist URL + wikilink REFERENCES edges, attributed per section.

    Each unit's URLs/wikilinks become RawEdges whose source_qualname is the
    unit's qualname; the symbol table maps those qualnames to the section
    memory_ids created in this run, so resolve_edges attaches each edge to the
    section it appeared in (not the whole file).
    """
    from ..chunking.base_chunker import RawEdge
    from ..chunking.markdown_chunker import _scan_urls
    from ..core.symbol_resolver import SymbolEntry, resolve_edges

    symbols = [SymbolEntry(memory_id=mid, qualname=q) for q, mid in qual_to_id.items()]
    raw_edges: list[RawEdge] = []
    for unit in units:
        mid = qual_to_id.get(unit.qualname)
        if mid is None:  # section was skipped (unchanged) — leave its edges as-is
            continue
        for url in _scan_urls(unit.content.splitlines()):
            raw_edges.append(RawEdge(unit.qualname, url, "REFERENCES", "INFERRED"))
        for wl in _extract_wikilink_edges(unit.content):
            raw_edges.append(RawEdge(unit.qualname, wl, "REFERENCES", "INFERRED"))

    if not raw_edges:
        return

    relations = resolve_edges(
        raw_edges,
        repo_id=repo_id,
        branch=branch,
        commit_sha=commit_sha,
        symbols=symbols,
    )
    if relations:
        memory_manager.storage_manager.insert_relations(relations)
