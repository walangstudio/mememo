"""Comprehension tools: passthrough-first repo Q&A and architectural overview.

Both turn the indexed graph + chunks into grounding for the host LLM, mirroring
tools/generate_diagram.py and tools/capture.py: when no LLM provider is configured
(the default), return a passthrough_prompt for the host model to answer in chat;
when a provider is configured, answer directly.

- ask:           hybrid-recall the most relevant code chunks for a question and hand
                 them back with numbered [n] file:line citations for a grounded answer.
- overview:      assemble a deterministic system map (subsystems, key symbols, dependency
                 edges, languages) plus the overview Mermaid, then ask the host to name
                 each subsystem's responsibility and how the pieces fit together.
- generate_wiki: build a full onboarding wiki (Overview / Architecture / per-subsystem
                 pages / Core API / Getting Started) from the same facts + source
                 excerpts + diagrams; host writes the Markdown.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from pydantic import BaseModel, Field

from ._repo_context import resolve_repo_branch

if TYPE_CHECKING:
    from ..core.llm_adapter import LLMAdapter
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# Grounding budget for `ask`: total source text fed to the model (bounded so the
# prompt stays in one cache-friendly request) and a per-chunk cap (so one giant
# chunk can't crowd out every other citation).
_ANSWER_CHAR_BUDGET = 9000
_PER_CHUNK_CHAR_CAP = 1600


# --------------------------------------------------------------------------
# ask — cited repo Q&A
# --------------------------------------------------------------------------


class AskCitation(BaseModel):
    index: int
    file: str
    lines: str = ""
    symbol: str = ""
    similarity: float = 0.0


class AskParams(BaseModel):
    question: str = Field(description="Natural-language question about the codebase.")
    repo_path: str | None = Field(
        default=None, description="Repo path to detect repo_id/branch from. CWD if None."
    )
    repo_id: str | None = Field(default=None, description="Override repo_id (skips detection).")
    branch: str | None = Field(default=None, description="Override branch (skips detection).")
    top_k: int = Field(
        default=8, ge=1, le=30, description="How many code chunks to ground the answer in."
    )


class AskResponse(BaseModel):
    success: bool
    question: str
    answer: str = ""
    citations: list[AskCitation] = Field(default_factory=list)
    truncated: bool = False
    # Passthrough: when no LLM is configured the host model answers
    # passthrough_prompt (passthrough=True); citations are still returned.
    passthrough: bool = False
    passthrough_prompt: str = ""
    message: str = ""


_ASK_SYSTEM = (
    "You answer questions about a codebase using ONLY the numbered code excerpts "
    "provided. Cite every claim with the source number in square brackets, e.g. [2], "
    "which maps to a file:line. If the excerpts do not contain the answer, say so "
    "plainly rather than guessing. Be concise and concrete."
)


def _cite_parts(content) -> tuple[str, str, str]:
    """Return (file, lines, symbol) for a memory's content, for citation display."""
    file = (content.file_path or "?").replace("\\", "/")
    lines = ""
    if content.line_range:
        lines = f"{content.line_range[0]}-{content.line_range[1]}"
    if content.class_name and content.function_name:
        sym = f"{content.class_name}.{content.function_name}"
    else:
        sym = content.function_name or content.class_name or ""
    return file, lines, sym


async def ask(
    params: AskParams,
    memory_manager: MemoryManager,
    llm_adapter: LLMAdapter,
) -> AskResponse:
    from ..types.memory import SearchParams

    repo_id, branch = await resolve_repo_branch(
        params.repo_id, params.branch, params.repo_path, memory_manager
    )

    results = await memory_manager.search_similar(
        SearchParams(
            query=params.question,
            top_k=params.top_k,
            # Code cosine scores run low; rank (not a threshold) decides the top_k,
            # and the BM25 half of hybrid surfaces exact identifiers.
            min_similarity=0.0,
            hybrid=True,
            repo_id=repo_id or None,
            branch=branch or None,
        )
    )
    if not results:
        return AskResponse(
            success=False,
            question=params.question,
            message=(
                "No indexed code matched. Index the repo first (index_repository), "
                "then ask again."
            ),
        )

    # Number citations by position among the chunks actually included, so the [n]
    # in the prompt always maps to citations[n-1] (skipped/over-budget chunks leave
    # no gap). Citation and source block are appended together to stay aligned.
    citations: list[AskCitation] = []
    blocks: list[str] = []
    used = 0
    truncated = False
    for r in results:
        content = r.memory.content
        text = (content.text or "").strip()
        if not text:
            continue  # nothing to quote or cite — skip rather than emit an empty [n]
        n = len(blocks) + 1
        file, lines, sym = _cite_parts(content)
        anchor = f"{file}:{lines}" if lines else file
        block = f"\n[{n}] {anchor}" + (f"  {sym}" if sym else "") + "\n"
        block += text[:_PER_CHUNK_CHAR_CAP] + "\n"
        # Stop once the budget is reached, but always include at least one block.
        if blocks and used + len(block) > _ANSWER_CHAR_BUDGET:
            truncated = True
            break
        citations.append(
            AskCitation(
                index=n, file=file, lines=lines, symbol=sym, similarity=round(r.similarity, 3)
            )
        )
        blocks.append(block)
        used += len(block)

    if not blocks:
        return AskResponse(
            success=False,
            question=params.question,
            message="Matched memories but none had readable source text to ground an answer.",
        )

    user_prompt = (
        f"Question: {params.question}\n\n"
        f"=== Sources ===\n{''.join(blocks)}\n\n"
        "Answer the question, citing sources as [n]."
    )

    if llm_adapter.is_passthrough():
        return AskResponse(
            success=True,
            question=params.question,
            citations=citations,
            truncated=truncated,
            passthrough=True,
            passthrough_prompt=f"{_ASK_SYSTEM}\n\n{user_prompt}",
            message="Passthrough — no LLM configured. Answer from passthrough_prompt (host model).",
        )

    answer = await llm_adapter.complete(_ASK_SYSTEM, user_prompt)
    if not (answer or "").strip():
        # complete() returned None (call failed) or empty — fall back to passthrough
        # rather than returning a blank answer; the prompt is still usable.
        return AskResponse(
            success=True,
            question=params.question,
            citations=citations,
            truncated=truncated,
            passthrough=True,
            passthrough_prompt=f"{_ASK_SYSTEM}\n\n{user_prompt}",
            message="LLM returned no answer — falling back to passthrough.",
        )

    return AskResponse(
        success=True,
        question=params.question,
        answer=answer.strip(),
        citations=citations,
        truncated=truncated,
    )


# --------------------------------------------------------------------------
# overview — architectural system map
# --------------------------------------------------------------------------


class OverviewSubsystem(BaseModel):
    name: str
    files: int
    chunks: int
    classes: list[str] = Field(default_factory=list)


class OverviewParams(BaseModel):
    repo_path: str | None = Field(
        default=None, description="Repo path to detect repo_id/branch from. CWD if None."
    )
    repo_id: str | None = Field(default=None, description="Override repo_id (skips detection).")
    branch: str | None = Field(default=None, description="Override branch (skips detection).")
    depth: int = Field(
        default=2, ge=1, le=4, description="Path-segment depth for subsystem grouping."
    )
    max_nodes: int = Field(
        default=40, ge=1, le=200, description="Subsystem node cap for the overview diagram."
    )


class OverviewResponse(BaseModel):
    success: bool
    repo_id: str = ""
    branch: str = ""
    mermaid: str = ""
    subsystems: list[OverviewSubsystem] = Field(default_factory=list)
    languages: dict[str, int] = Field(default_factory=dict)
    key_symbols: list[str] = Field(default_factory=list)
    edge_counts: dict[str, int] = Field(default_factory=dict)
    answer: str = ""
    truncated: bool = False
    passthrough: bool = False
    passthrough_prompt: str = ""
    message: str = ""


_OVERVIEW_SYSTEM = (
    "You write a concise architectural overview of a codebase for a new contributor, "
    "using ONLY the structural facts provided (subsystems, key symbols, dependency edges, "
    "languages). For each major subsystem, name it and describe its responsibility in one "
    "or two sentences. Then add a short 'how it fits together' paragraph tracing the main "
    "flow. Do not invent components, files, or behaviour absent from the facts."
)

# How many of the most-called symbols to surface as the "core API".
_MAX_KEY_SYMBOLS = 15


class _RepoFacts(NamedTuple):
    """Deterministic structural facts shared by `overview` and `generate_wiki`."""

    subsystems: list[OverviewSubsystem]
    languages: dict[str, int]
    edge_counts: dict[str, int]
    key_symbols: list[str]
    key_symbol_ids: list[str]  # memory ids behind key_symbols, for source reading
    key_symbol_files: list[str]  # file_path per key symbol, for scope filtering
    overview_mermaid: str  # "" when the graph has no cross-subsystem edges
    truncated: bool  # subsystem list capped OR diagram capped


def _gather_facts(
    conn: sqlite3.Connection, repo_id: str, branch: str, depth: int, max_nodes: int
) -> _RepoFacts | None:
    """Compute subsystems / languages / edges / core-API for a lane, or None if unindexed."""
    from ..diagrams import _label, _subsystem, is_empty_diagram, overview_diagram

    file_rows = conn.execute(
        "SELECT file_path, class_name FROM memories "
        "WHERE repo_id = ? AND branch_name = ? AND stale = 0 "
        "AND content_type = 'code_snippet' AND file_path IS NOT NULL",
        (repo_id, branch),
    ).fetchall()
    if not file_rows:
        return None

    subs: dict[str, dict] = {}
    for r in file_rows:
        name = _subsystem(r["file_path"], depth)
        d = subs.setdefault(name, {"files": set(), "chunks": 0, "classes": []})
        d["files"].add(r["file_path"])
        d["chunks"] += 1
        cn = r["class_name"]
        if cn and cn not in d["classes"] and len(d["classes"]) < 8:
            d["classes"].append(cn)
    # Cap by the same knob as the diagram so the facts and the Mermaid agree on how
    # many subsystems they show; biggest-by-file-count first. Dropping any sets
    # truncated so the caller isn't told a partial map is complete.
    subs_sorted = sorted(subs.items(), key=lambda kv: len(kv[1]["files"]), reverse=True)
    subsystems = [
        OverviewSubsystem(name=n, files=len(d["files"]), chunks=d["chunks"], classes=d["classes"])
        for n, d in subs_sorted[:max_nodes]
    ]
    subsystems_truncated = len(subs_sorted) > max_nodes

    lang_rows = conn.execute(
        "SELECT language, COUNT(*) c FROM memories "
        "WHERE repo_id = ? AND branch_name = ? AND stale = 0 AND language IS NOT NULL "
        "GROUP BY language ORDER BY c DESC",
        (repo_id, branch),
    ).fetchall()
    languages = {r["language"]: r["c"] for r in lang_rows}

    try:
        edge_rows = conn.execute(
            "SELECT type, COUNT(*) c FROM relations "
            "WHERE repo_id = ? AND branch = ? AND stale = 0 GROUP BY type ORDER BY c DESC",
            (repo_id, branch),
        ).fetchall()
        edge_counts = {r["type"]: r["c"] for r in edge_rows}
    except sqlite3.OperationalError:
        edge_counts = {}

    # Most-called symbols = the de-facto core API (highest CALLS in-degree).
    key_rows = conn.execute(
        "SELECT m.id, m.file_path, m.class_name, m.function_name, COUNT(*) c "
        "FROM relations r JOIN memories m ON m.id = r.target_memory_id "
        "WHERE r.repo_id = ? AND r.branch = ? AND r.type = 'CALLS' AND r.stale = 0 "
        "AND r.target_memory_id IS NOT NULL "
        "GROUP BY r.target_memory_id ORDER BY c DESC LIMIT ?",
        (repo_id, branch, _MAX_KEY_SYMBOLS),
    ).fetchall()
    key_symbols = [
        f"{_label(r['file_path'], r['class_name'], r['function_name'])} (x{r['c']})"
        for r in key_rows
    ]
    key_symbol_ids = [r["id"] for r in key_rows]
    key_symbol_files = [r["file_path"] or "" for r in key_rows]

    raw = overview_diagram(conn, repo_id, branch, max_nodes=max_nodes, depth=depth)
    overview_mermaid = raw if not is_empty_diagram(raw) else ""
    truncated = subsystems_truncated or "%% truncated" in raw

    return _RepoFacts(
        subsystems,
        languages,
        edge_counts,
        key_symbols,
        key_symbol_ids,
        key_symbol_files,
        overview_mermaid,
        truncated,
    )


async def overview(
    params: OverviewParams,
    memory_manager: MemoryManager,
    llm_adapter: LLMAdapter,
) -> OverviewResponse:
    repo_id, branch = await resolve_repo_branch(
        params.repo_id, params.branch, params.repo_path, memory_manager
    )
    if not repo_id or not branch:
        return OverviewResponse(
            success=False,
            repo_id=repo_id,
            branch=branch,
            message=(
                "Could not determine repo context. Pass repo_id and branch, or run from "
                "inside the indexed repo."
            ),
        )

    facts = _gather_facts(
        memory_manager.storage_manager.conn, repo_id, branch, params.depth, params.max_nodes
    )
    if facts is None:
        return OverviewResponse(
            success=False,
            repo_id=repo_id,
            branch=branch,
            message="No indexed code. Index the repo first (index_repository), then retry.",
        )

    user_prompt = (
        f"=== Codebase structural facts ===\n"
        f"{_overview_facts(facts.subsystems, facts.languages, facts.edge_counts, facts.key_symbols)}\n"
        + (
            f"\n=== Subsystem dependency diagram (Mermaid) ===\n{facts.overview_mermaid}\n"
            if facts.overview_mermaid
            else ""
        )
        + "\nWrite the architectural overview."
    )

    def _resp(**kw) -> OverviewResponse:
        return OverviewResponse(
            success=True,
            repo_id=repo_id,
            branch=branch,
            mermaid=facts.overview_mermaid,
            subsystems=facts.subsystems,
            languages=facts.languages,
            key_symbols=facts.key_symbols,
            edge_counts=facts.edge_counts,
            truncated=facts.truncated,
            **kw,
        )

    prompt = f"{_OVERVIEW_SYSTEM}\n\n{user_prompt}"
    if llm_adapter.is_passthrough():
        return _resp(
            passthrough=True,
            passthrough_prompt=prompt,
            message="Passthrough — no LLM configured. Write the overview from passthrough_prompt (host model).",
        )

    answer = await llm_adapter.complete(_OVERVIEW_SYSTEM, user_prompt)
    if not (answer or "").strip():
        return _resp(
            passthrough=True,
            passthrough_prompt=prompt,
            message="LLM returned no overview — falling back to passthrough.",
        )
    return _resp(answer=answer.strip())


def _overview_facts(
    subsystems: list[OverviewSubsystem],
    languages: dict[str, int],
    edge_counts: dict[str, int],
    key_symbols: list[str],
) -> str:
    parts: list[str] = []
    if languages:
        parts.append("Languages (chunks): " + ", ".join(f"{k} {v}" for k, v in languages.items()))
    if edge_counts:
        parts.append("Graph edges: " + ", ".join(f"{k} {v}" for k, v in edge_counts.items()))
    parts.append("\nSubsystems (by file count):")
    for s in subsystems:
        line = f"- {s.name}: {s.files} files, {s.chunks} chunks"
        if s.classes:
            line += " — classes: " + ", ".join(s.classes)
        parts.append(line)
    if key_symbols:
        parts.append("\nMost-called symbols (core API):")
        parts.extend(f"- {sym}" for sym in key_symbols)
    return "\n".join(parts)


# --------------------------------------------------------------------------
# generate_wiki — auto-generated onboarding wiki (passthrough-first)
# --------------------------------------------------------------------------

# Wiki grounding is larger than ask/overview (multi-section doc), but still bounded
# so the passthrough_prompt stays one request; per-excerpt cap keeps any one core-API
# symbol from dominating.
_WIKI_CHAR_BUDGET = 16000
_WIKI_SOURCE_CAP = 1200
_WIKI_SECTIONS = ["Overview", "Architecture", "Subsystems", "Core API", "Getting Started"]

_WIKI_SYSTEM = (
    "You write a developer wiki / onboarding guide for a codebase in Markdown, using ONLY the "
    "provided structural facts, source excerpts, and diagrams. Produce one Markdown document "
    "with, in order: a top-level `# <Project> — Overview` and a 2-4 sentence 'what this is and "
    "what it does'; a `## Architecture` section that OPENS with a high-level Mermaid `flowchart TD` "
    "a non-technical business reader and an engineer can both follow — show the main inputs, the "
    "end-to-end happy-path of what the system DOES, and the outputs, with friendly plain-English "
    "labels describing each part's job (describe behaviour, not file names, class names, or "
    "imports), grouping related steps with subgraphs; you DRAW this flowchart yourself from the "
    "README and the subsystem responsibilities, then add 2-3 sentences on how the pieces fit; a "
    "`## Subsystems` section with one `### <name>` subsection per subsystem naming its "
    "responsibility in 1-2 sentences and listing its key classes; a `## Core API` section "
    "describing the most-called symbols and what each does; and a `## Getting Started` section "
    "pointing at the likely entry points. If the grounding includes any deterministic diagrams, "
    "embed each verbatim in a ```mermaid block under a final `## Appendix: Module map (for "
    "engineers)` section — keep them OUT of the high-level Architecture flowchart. Reference files "
    "and symbols in `backticks`. Do NOT invent components, files, behaviour, or APIs absent from "
    "the grounding — if something isn't in the grounding, omit it."
)


class WikiParams(BaseModel):
    repo_path: str | None = Field(
        default=None,
        description="Repo path to detect repo_id/branch from (and read README). CWD if None.",
    )
    repo_id: str | None = Field(default=None, description="Override repo_id (skips detection).")
    branch: str | None = Field(default=None, description="Override branch (skips detection).")
    scope: str | None = Field(
        default=None,
        description="Limit the wiki to subsystems whose path matches this prefix/substring. None = whole repo.",
    )
    depth: int = Field(
        default=2, ge=1, le=4, description="Path-segment depth for subsystem grouping."
    )
    max_nodes: int = Field(
        default=40, ge=1, le=200, description="Subsystem cap (shared by facts + diagram)."
    )
    include_source: bool = Field(
        default=True, description="Embed source excerpts of the core-API symbols in the grounding."
    )
    write_path: str | None = Field(
        default=None,
        description="When an LLM provider IS configured, write the generated Markdown here (e.g. WIKI.md). Ignored in passthrough.",
    )


class WikiResponse(BaseModel):
    success: bool
    repo_id: str = ""
    branch: str = ""
    sections: list[str] = Field(default_factory=list)
    diagrams: dict[str, str] = Field(default_factory=dict)
    wiki: str = ""
    written_to: str = ""
    truncated: bool = False
    passthrough: bool = False
    passthrough_prompt: str = ""
    message: str = ""


async def generate_wiki(
    params: WikiParams,
    memory_manager: MemoryManager,
    llm_adapter: LLMAdapter,
) -> WikiResponse:
    from ..diagrams import _subsystem, module_dependency
    from .generate_diagram import _read_source

    repo_id, branch = await resolve_repo_branch(
        params.repo_id, params.branch, params.repo_path, memory_manager
    )
    if not repo_id or not branch:
        return WikiResponse(
            success=False,
            repo_id=repo_id,
            branch=branch,
            message=(
                "Could not determine repo context. Pass repo_id and branch, or run from "
                "inside the indexed repo."
            ),
        )

    storage = memory_manager.storage_manager
    conn = storage.conn
    facts = _gather_facts(conn, repo_id, branch, params.depth, params.max_nodes)
    if facts is None:
        return WikiResponse(
            success=False,
            repo_id=repo_id,
            branch=branch,
            message="No indexed code. Index the repo first (index_repository), then retry.",
        )

    # Scope narrows ALL the grounding (subsystems, core symbols, excerpts) to the
    # matching subsystems so the model never documents out-of-scope code; the
    # whole-repo diagrams are dropped for a scoped page (they'd show other areas).
    subsystems = facts.subsystems
    key_symbols = facts.key_symbols
    key_symbol_ids = facts.key_symbol_ids
    truncated = facts.truncated
    diagrams: dict[str, str] = {}
    if params.scope:
        subsystems = [s for s in subsystems if params.scope in s.name]
        if not subsystems:
            return WikiResponse(
                success=False,
                repo_id=repo_id,
                branch=branch,
                message=f"No subsystem matches scope={params.scope!r}. Try a broader path prefix.",
            )
        scoped = {s.name for s in subsystems}
        keep = [
            i for i, f in enumerate(facts.key_symbol_files) if _subsystem(f, params.depth) in scoped
        ]
        key_symbols = [facts.key_symbols[i] for i in keep]
        key_symbol_ids = [facts.key_symbol_ids[i] for i in keep]
    else:
        if facts.overview_mermaid:
            diagrams["overview"] = facts.overview_mermaid
        modules = module_dependency(conn, repo_id, branch, max_nodes=params.max_nodes)
        if "%% no data" not in modules:
            diagrams["modules"] = modules
            truncated = truncated or "%% truncated" in modules

    # Grounding: README (plain-language project description) + structural facts +
    # core-API source excerpts + the diagrams the wiki should embed.
    parts: list[str] = []
    if params.repo_path:
        try:
            readme = (Path(params.repo_path) / "README.md").read_text(
                encoding="utf-8", errors="replace"
            )
            parts.append("# README:\n" + readme[:2500])
        except OSError:
            pass  # no README / unreadable — degrade gracefully
    parts.append(
        "# Structural facts:\n"
        + _overview_facts(subsystems, facts.languages, facts.edge_counts, key_symbols)
    )

    if params.include_source:
        used = sum(len(p) for p in parts)
        blocks: list[str] = []
        for mid in key_symbol_ids:
            text, label = _read_source(storage, conn, mid)
            if not text:
                continue
            block = f"\n## {label}\n{text[:_WIKI_SOURCE_CAP]}\n"
            if blocks and used + len(block) > _WIKI_CHAR_BUDGET:
                truncated = True
                break
            blocks.append(block)
            used += len(block)
        if blocks:
            parts.append("\n# Core API source excerpts:")
            parts.extend(blocks)

    for name, mmd in diagrams.items():
        parts.append(f"\n# {name} diagram (Mermaid):\n{mmd}")

    # The deterministic module/overview graphs ship as an engineers-only appendix
    # (the high-level Architecture flowchart is what onboards a business reader).
    sections = list(_WIKI_SECTIONS)
    if diagrams:
        sections.append("Appendix: Module map")
    target = f"subsystem {params.scope!r}" if params.scope else "this repository"
    user_prompt = (
        f"Write the wiki for {target}.\n"
        f"Section plan: {', '.join(sections)}\n\n"
        f"=== Grounding ===\n{''.join(parts)}"
    )

    def _resp(**kw) -> WikiResponse:
        return WikiResponse(
            success=True,
            repo_id=repo_id,
            branch=branch,
            sections=sections,
            diagrams=diagrams,
            truncated=truncated,
            **kw,
        )

    prompt = f"{_WIKI_SYSTEM}\n\n{user_prompt}"
    if llm_adapter.is_passthrough():
        return _resp(
            passthrough=True,
            passthrough_prompt=prompt,
            message="Passthrough — no LLM configured. Write the wiki from passthrough_prompt (host model).",
        )

    wiki = await llm_adapter.complete(_WIKI_SYSTEM, user_prompt)
    if not (wiki or "").strip():
        return _resp(
            passthrough=True,
            passthrough_prompt=prompt,
            message="LLM returned no wiki — falling back to passthrough.",
        )
    wiki = wiki.strip()

    written = ""
    message = ""
    if params.write_path:
        # Confine the write to the repo root so a caller-supplied path can't clobber
        # files elsewhere (a relative path resolves under the repo; '..' escapes are
        # refused). repo_path unset -> root is CWD.
        root = Path(params.repo_path or ".").resolve()
        dest = Path(params.write_path)
        dest = (dest if dest.is_absolute() else root / dest).resolve()
        try:
            dest.relative_to(root)
        except ValueError:
            return _resp(
                wiki=wiki,
                message=(
                    f"Generated the wiki but refused to write {params.write_path!r}: "
                    f"resolves outside the repo root {str(root)!r}."
                ),
            )
        try:
            dest.write_text(wiki, encoding="utf-8")
            written = str(dest)
        except OSError as exc:
            message = f"Generated the wiki but could not write {str(dest)!r}: {exc}"

    return _resp(wiki=wiki, written_to=written, message=message)
