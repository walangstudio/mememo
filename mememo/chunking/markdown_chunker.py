"""Markdown chunker — heading-scoped sections + doc->code edges (v0.7).

Pure-Python (no tree-sitter): splits a Markdown file on ATX headings
(``#``..``######``) into one chunk per section, tracking a heading-level
stack so each section records its parent heading. Fenced code blocks are
left inside their section (and skipped when scanning for symbol mentions).

``chunk_with_edges`` additionally emits ``DOCUMENTS`` edges from a doc
section to code symbols it names — backtick-quoted identifiers in prose
(`` `MyClass` ``, `` `do_thing()` ``) and bare ``path/to/file.ext``
mentions. These are INFERRED-confidence: the symbol_resolver matches them
against the indexed code symbol table and drops the ones that don't resolve.
"""

from __future__ import annotations

import re

from .base_chunker import BaseChunker, Chunk, RawEdge
from .python_ast_chunker import file_path_to_module

# ATX heading: 1-6 leading '#', a space, then the text. (Setext "===" headings
# are not handled — rare in code repos.)
_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*\s*$")
_FENCE_RE = re.compile(r"^([ \t]*)(`{3,}|~{3,})")

# Backtick-quoted code spans in prose: `Foo`, `foo()`, `a.b.c`.
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
# A token that looks like a code symbol (optionally dotted/scoped, optional call
# parens). Filters out plain prose and most non-identifiers.
_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:[.:]{1,2}[A-Za-z_][A-Za-z0-9_]*)*(?:\(\))?$")
# A bare file path mention: at least one '/' and a dotted extension.
_PATH_RE = re.compile(r"\b([\w./-]+/[\w.-]+\.[A-Za-z0-9]+)\b")


def _slug(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s or "section"


def _symbol_from_span(span: str) -> str | None:
    """Normalise an inline-code span to a code symbol target, or None."""
    tok = span.strip()
    if not _SYMBOL_RE.match(tok):
        return None
    tok = tok.rstrip("()")
    # Drop bare lowercase one-word spans that are almost always prose/keywords
    # (`true`, `null`, `id`); keep dotted/scoped/CamelCase/with-call forms.
    if "." not in tok and ":" not in tok and tok.islower() and "(" not in span:
        if not span.endswith("()"):
            return None
    return tok or None


class MarkdownChunker(BaseChunker):
    """Heading-scoped Markdown chunker with doc->code edge emission."""

    def chunk(self, code: str, file_path: str) -> list[Chunk]:
        return self._walk(code, file_path)[0]

    def chunk_with_edges(self, code: str, file_path: str) -> tuple[list[Chunk], list[RawEdge]]:
        return self._walk(code, file_path)

    def _walk(self, code: str, file_path: str) -> tuple[list[Chunk], list[RawEdge]]:
        module = file_path_to_module(file_path)
        lines = code.splitlines()

        chunks: list[Chunk] = []
        edges: list[RawEdge] = []

        # Preamble (content before the first heading) becomes a "text" chunk so
        # nothing is silently dropped.
        preamble: list[str] = []
        heading_stack: list[tuple[int, str, str]] = []  # (level, heading, slug)
        open_sections: list[dict] = []
        in_fence = False
        fence_marker = ""

        def flush(section: dict) -> None:
            body = section["body"]
            text = "\n".join(body).rstrip()
            start = section["start"] + 1
            end = section["start"] + max(len(body), 1)
            parent = section["parent_heading"]
            qual_parts = [module, *section["slug_path"]]
            chunks.append(
                Chunk(
                    text=(
                        ("#" * section["level"] + " " + section["heading"] + "\n" + text)
                        if text
                        else "#" * section["level"] + " " + section["heading"]
                    ),
                    start_line=start,
                    end_line=end,
                    chunk_type="heading",
                    function_name=section["heading"],
                    parent_class=parent,
                    language="markdown",
                    file_path=file_path,
                )
            )
            source_qual = ".".join(qual_parts)
            for target in _scan_symbols(body):
                edges.append(RawEdge(source_qual, target, "DOCUMENTS", "INFERRED"))

        for idx, line in enumerate(lines):
            fence_m = _FENCE_RE.match(line)
            if fence_m:
                marker = fence_m.group(2)
                if not in_fence:
                    in_fence, fence_marker = True, marker[0]
                elif marker[0] == fence_marker:
                    in_fence = False
                if open_sections:
                    open_sections[-1]["body"].append(line)
                else:
                    preamble.append(line)
                continue

            heading_m = None if in_fence else _HEADING_RE.match(line)
            if heading_m is None:
                if open_sections:
                    open_sections[-1]["body"].append(line)
                else:
                    preamble.append(line)
                continue

            level = len(heading_m.group(1))
            heading = heading_m.group(2).strip()
            # Close sections at this level or deeper.
            while open_sections and open_sections[-1]["level"] >= level:
                flush(open_sections.pop())
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            parent_heading = heading_stack[-1][1] if heading_stack else None
            slug = _slug(heading)
            slug_path = [sl for _, _, sl in heading_stack] + [slug]
            heading_stack.append((level, heading, slug))
            open_sections.append(
                {
                    "level": level,
                    "heading": heading,
                    "slug": slug,
                    "slug_path": slug_path,
                    "parent_heading": parent_heading,
                    "start": idx,
                    "body": [],
                }
            )

        while open_sections:
            flush(open_sections.pop())

        # Emit preamble as a leading text chunk if it has real content.
        pre_text = "\n".join(preamble).strip()
        if pre_text:
            chunks.insert(
                0,
                Chunk(
                    text=pre_text,
                    start_line=1,
                    end_line=len(preamble),
                    chunk_type="text",
                    language="markdown",
                    file_path=file_path,
                ),
            )

        # Stable order: by start line.
        chunks.sort(key=lambda c: c.start_line)
        return chunks, edges


def _scan_symbols(body_lines: list[str]) -> list[str]:
    """Collect doc->code symbol targets from a section body (skipping fences)."""
    targets: list[str] = []
    seen: set[str] = set()
    in_fence = False
    fence_marker = ""
    for line in body_lines:
        fence_m = _FENCE_RE.match(line)
        if fence_m:
            marker = fence_m.group(2)
            if not in_fence:
                in_fence, fence_marker = True, marker[0]
            elif marker[0] == fence_marker:
                in_fence = False
            continue
        if in_fence:
            continue
        for span in _INLINE_CODE_RE.findall(line):
            sym = _symbol_from_span(span)
            if sym and sym not in seen:
                seen.add(sym)
                targets.append(sym)
        for path in _PATH_RE.findall(line):
            if path not in seen:
                seen.add(path)
                targets.append(path)
    return targets
