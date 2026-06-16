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

from ..types import RelationType
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


# --------------------------------------------------------------------------
# explore — agentic multi-hop graph traversal toward a goal
# --------------------------------------------------------------------------

# Per-hop score falloff: a node two hops out is worth DECAY**2 of its seed, so
# semantic seeds outrank distant nodes when the budget forces a cut.
_EXPLORE_DECAY = 0.6
_EXPLORE_TRACE_CAP = 40


def _in_chunks(items, size):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _node_meta(conn, mids) -> dict[str, tuple[str, str, str]]:
    """memory id -> (file_path, class_name, function_name) for labels + citations."""
    meta: dict[str, tuple[str, str, str]] = {}
    for chunk in _in_chunks(mids, 400):
        ph = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"SELECT id, file_path, class_name, function_name FROM memories WHERE id IN ({ph})",
            chunk,
        ).fetchall():
            meta[r["id"]] = (r["file_path"] or "", r["class_name"] or "", r["function_name"] or "")
    return meta


class ExploreHop(BaseModel):
    source: str
    type: str
    target: str
    depth: int


class ExploreParams(BaseModel):
    goal: str = Field(description="What to understand or trace through the codebase.")
    repo_path: str | None = Field(
        default=None, description="Repo path to detect repo_id/branch from. CWD if None."
    )
    repo_id: str | None = Field(default=None, description="Override repo_id (skips detection).")
    branch: str | None = Field(default=None, description="Override branch (skips detection).")
    max_hops: int = Field(
        default=3, ge=1, le=6, description="How many edges deep to traverse from the seed nodes."
    )
    beam: int = Field(
        default=6, ge=1, le=20, description="Seed count and how many new nodes to keep per hop."
    )
    max_nodes: int = Field(
        default=40, ge=1, le=100, description="Total node budget for the explored subgraph."
    )
    edge_types: list[RelationType] | None = Field(
        default=None, description="Restrict traversal to these edge types (default: all)."
    )


class ExploreResponse(BaseModel):
    success: bool
    goal: str
    answer: str = ""
    citations: list[AskCitation] = Field(default_factory=list)
    trace: list[ExploreHop] = Field(default_factory=list)
    nodes_visited: int = 0
    hops: int = 0
    truncated: bool = False
    passthrough: bool = False
    passthrough_prompt: str = ""
    message: str = ""


_EXPLORE_SYSTEM = (
    "You explain how a codebase accomplishes something by reasoning over a multi-hop slice "
    "of its call/dependency graph. Use ONLY the numbered code excerpts and the traversal "
    "trace provided. Trace the path from the entry points to the goal, citing every claim "
    "with the source number in square brackets, e.g. [2]. If the slice does not reach the "
    "answer, say what's missing rather than guessing. Be concise and concrete."
)


async def explore(
    params: ExploreParams,
    memory_manager: MemoryManager,
    llm_adapter: LLMAdapter,
) -> ExploreResponse:
    """Seed on the goal, beam-expand the graph multi-hop, synthesise from the slice."""
    from ..diagrams import _label
    from ..types.memory import SearchParams
    from .generate_diagram import _read_source

    repo_id, branch = await resolve_repo_branch(
        params.repo_id, params.branch, params.repo_path, memory_manager
    )
    if not repo_id or not branch:
        return ExploreResponse(
            success=False,
            goal=params.goal,
            message=(
                "Could not determine repo context. Pass repo_id and branch, or run from "
                "inside the indexed repo."
            ),
        )

    storage = memory_manager.storage_manager
    conn = storage.conn

    # Seed at the most goal-relevant chunks (semantic + lexical), then walk outward.
    seeds = await memory_manager.search_similar(
        SearchParams(
            query=params.goal,
            top_k=params.beam,
            min_similarity=0.0,
            hybrid=True,
            repo_id=repo_id or None,
            branch=branch or None,
        )
    )
    if not seeds:
        return ExploreResponse(
            success=False,
            goal=params.goal,
            message=(
                "No indexed code matched the goal. Index the repo first (index_repository), "
                "then explore."
            ),
        )

    scores: dict[str, float] = {}
    for r in seeds:
        scores[r.memory.id] = max(scores.get(r.memory.id, 0.0), float(r.similarity))
    visited: set[str] = set(scores)
    frontier: list[str] = list(scores)
    type_filter = set(params.edge_types) if params.edge_types else None
    raw_trace: list[tuple[str, str | None, str | None, str, int]] = []
    seen_edges: set[tuple[str, str | None, str | None, str]] = set()
    truncated = False
    hops_done = 0

    for hop in range(1, params.max_hops + 1):
        if not frontier or len(visited) >= params.max_nodes:
            break
        hops_done = hop
        cand: dict[str, float] = {}
        edges: list[tuple[str, str | None, str | None, str]] = []
        for ids in _in_chunks(frontier, 400):
            ph = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT source_memory_id, target_memory_id, target_symbol, type "
                f"FROM relations WHERE repo_id = ? AND branch = ? AND stale = 0 "
                f"AND (source_memory_id IN ({ph}) OR target_memory_id IN ({ph}))",
                (repo_id, branch, *ids, *ids),
            ).fetchall()
            for row in rows:
                etype = row["type"]
                if type_filter and etype not in type_filter:
                    continue
                src, tgt, sym = (
                    row["source_memory_id"],
                    row["target_memory_id"],
                    row["target_symbol"],
                )
                edges.append((src, tgt, sym, etype))
                # The "new" endpoint inherits a decayed share of its visited parent's score.
                for node, parent in ((tgt, src), (src, tgt)):
                    if node and node not in visited:
                        base = scores.get(parent, 0.0)
                        cand[node] = max(cand.get(node, 0.0), base * _EXPLORE_DECAY)
        if not edges:
            break
        # Beam-prune the newly reached nodes by score, capped by the remaining budget.
        ranked = sorted(cand.items(), key=lambda kv: kv[1], reverse=True)
        # room is >= 1 here (the loop-top guard breaks once visited hits max_nodes);
        # max(0, ...) keeps the slice well-defined if that invariant ever changes.
        room = max(0, params.max_nodes - len(visited))
        keep = ranked[: min(params.beam, room)]
        if len(ranked) > len(keep):
            truncated = True
        for node, sc in keep:
            scores[node] = max(scores.get(node, 0.0), sc)
            visited.add(node)
        for src, tgt, sym, etype in edges:
            if len(raw_trace) >= _EXPLORE_TRACE_CAP:
                truncated = True
                break
            key = (src, tgt, sym, etype)
            if key in seen_edges:
                continue
            if src in visited and (tgt in visited or (tgt is None and sym)):
                seen_edges.add(key)
                raw_trace.append((src, tgt, sym, etype, hop))
        frontier = [node for node, _ in keep]

    # Goal re-rank: blend the graph score with a direct vector similarity so an
    # on-target node deep in the slice still outranks a barely-relevant seed.
    rerank = await memory_manager.search_similar(
        SearchParams(
            query=params.goal,
            top_k=min(params.max_nodes, 100),
            min_similarity=0.0,
            hybrid=True,
            repo_id=repo_id or None,
            branch=branch or None,
        )
    )
    vec = {r.memory.id: float(r.similarity) for r in rerank}
    ranked_nodes = sorted(visited, key=lambda m: scores.get(m, 0.0) + vec.get(m, 0.0), reverse=True)

    meta = _node_meta(conn, visited)

    def _label_of(mid: str | None, sym: str | None = None) -> str:
        if mid and mid in meta:
            f, c, fn = meta[mid]
            return _label(f, c, fn)
        return sym or (mid[:8] if mid else "?")

    citations: list[AskCitation] = []
    blocks: list[str] = []
    used = 0
    for mid in ranked_nodes:
        text, _lab = _read_source(storage, conn, mid)
        text = (text or "").strip()
        if not text:
            continue
        n = len(blocks) + 1
        f, c, fn = meta.get(mid, ("", "", ""))
        file = (f or "?").replace("\\", "/")
        sym = f"{c}.{fn}" if c and fn else (fn or c or "")
        block = (
            f"\n[{n}] {file}"
            + (f"  {sym}" if sym else "")
            + "\n"
            + text[:_PER_CHUNK_CHAR_CAP]
            + "\n"
        )
        if blocks and used + len(block) > _ANSWER_CHAR_BUDGET:
            truncated = True
            break
        citations.append(
            AskCitation(
                index=n,
                file=file,
                symbol=sym,
                similarity=round(scores.get(mid, 0.0) + vec.get(mid, 0.0), 3),
            )
        )
        blocks.append(block)
        used += len(block)

    hops = [
        ExploreHop(source=_label_of(src), type=etype, target=_label_of(tgt, sym), depth=depth)
        for src, tgt, sym, etype, depth in raw_trace
    ]
    trace_lines = "\n".join(f"{h.source} --{h.type}--> {h.target}" for h in hops)

    user_prompt = (
        f"Goal: {params.goal}\n\n"
        f"=== Traversal (multi-hop graph slice, {len(visited)} nodes) ===\n"
        f"{trace_lines or '(no edges from the seed nodes — the seeds are isolated)'}\n\n"
        f"=== Sources ===\n{''.join(blocks)}\n\n"
        "Explain how the code achieves the goal, citing sources as [n]."
    )

    def _resp(**kw) -> ExploreResponse:
        return ExploreResponse(
            success=True,
            goal=params.goal,
            citations=citations,
            trace=hops,
            nodes_visited=len(visited),
            hops=hops_done,
            truncated=truncated,
            **kw,
        )

    if not blocks:
        return _resp(
            message="Explored the graph but no reached node had readable source to ground an answer."
        )

    if llm_adapter.is_passthrough():
        return _resp(
            passthrough=True,
            passthrough_prompt=f"{_EXPLORE_SYSTEM}\n\n{user_prompt}",
            message="Passthrough — no LLM configured. Answer from passthrough_prompt (host model).",
        )

    answer = await llm_adapter.complete(_EXPLORE_SYSTEM, user_prompt)
    if not (answer or "").strip():
        return _resp(
            passthrough=True,
            passthrough_prompt=f"{_EXPLORE_SYSTEM}\n\n{user_prompt}",
            message="LLM returned no answer — falling back to passthrough.",
        )
    return _resp(answer=answer.strip())


# --------------------------------------------------------------------------
# project_prompt — synthesise a reusable project primer from the indexed repo
# --------------------------------------------------------------------------

_PROJECT_PROMPT_SOURCE_CAP = 1000
_PROJECT_PROMPT_BUDGET = 11000


class ProjectPromptParams(BaseModel):
    repo_path: str | None = Field(
        default=None,
        description="Repo path to detect repo_id/branch from (and read README). CWD if None.",
    )
    repo_id: str | None = Field(default=None, description="Override repo_id (skips detection).")
    branch: str | None = Field(default=None, description="Override branch (skips detection).")
    focus: str | None = Field(
        default=None,
        description="Bias the primer toward an area or task, e.g. 'testing conventions'. None = general.",
    )
    depth: int = Field(
        default=2, ge=1, le=4, description="Path-segment depth for subsystem grouping."
    )
    max_nodes: int = Field(default=40, ge=1, le=200, description="Subsystem cap for the facts.")
    include_source: bool = Field(
        default=False, description="Include a few core-API source excerpts in the grounding."
    )


class ProjectPromptResponse(BaseModel):
    success: bool
    repo_id: str = ""
    branch: str = ""
    prompt: str = ""
    languages: dict[str, int] = Field(default_factory=dict)
    key_symbols: list[str] = Field(default_factory=list)
    truncated: bool = False
    passthrough: bool = False
    passthrough_prompt: str = ""
    message: str = ""


_PROJECT_PROMPT_SYSTEM = (
    "You write a concise, reusable project primer — a system prompt that briefs an AI coding "
    "agent BEFORE it works on this repository — using ONLY the provided structural facts, "
    "README, and source excerpts. Write it in the imperative, addressed to the agent ('You are "
    "working on ...'). Include, in order: one line on what the project is and its stack/"
    "languages; the architecture in 2-4 bullets (the main subsystems and how they relate); the "
    "key modules/symbols to know (the core API) and the likely entry points; and any "
    "conventions that are evident from the structure. Be terse and concrete. Do NOT invent "
    "components, files, behaviour, or conventions that are not in the grounding."
)


async def project_prompt(
    params: ProjectPromptParams,
    memory_manager: MemoryManager,
    llm_adapter: LLMAdapter,
) -> ProjectPromptResponse:
    """Turn the indexed repo's facts into a ready-to-paste project/system primer."""
    from .generate_diagram import _read_source

    repo_id, branch = await resolve_repo_branch(
        params.repo_id, params.branch, params.repo_path, memory_manager
    )
    if not repo_id or not branch:
        return ProjectPromptResponse(
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
        return ProjectPromptResponse(
            success=False,
            repo_id=repo_id,
            branch=branch,
            message="No indexed code. Index the repo first (index_repository), then retry.",
        )

    truncated = facts.truncated
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
        + _overview_facts(facts.subsystems, facts.languages, facts.edge_counts, facts.key_symbols)
    )

    if params.include_source:
        used = sum(len(p) for p in parts)
        blocks: list[str] = []
        for mid in facts.key_symbol_ids:
            text, label = _read_source(storage, conn, mid)
            if not text:
                continue
            block = f"\n## {label}\n{text[:_PROJECT_PROMPT_SOURCE_CAP]}\n"
            if blocks and used + len(block) > _PROJECT_PROMPT_BUDGET:
                truncated = True
                break
            blocks.append(block)
            used += len(block)
        if blocks:
            parts.append("\n# Core API source excerpts:")
            parts.extend(blocks)

    focus_line = f"\nFocus the primer on: {params.focus}\n" if params.focus else ""
    user_prompt = f"Write the project primer.{focus_line}\n=== Grounding ===\n{''.join(parts)}"

    def _resp(**kw) -> ProjectPromptResponse:
        return ProjectPromptResponse(
            success=True,
            repo_id=repo_id,
            branch=branch,
            languages=facts.languages,
            key_symbols=facts.key_symbols,
            truncated=truncated,
            **kw,
        )

    prompt = f"{_PROJECT_PROMPT_SYSTEM}\n\n{user_prompt}"
    if llm_adapter.is_passthrough():
        return _resp(
            passthrough=True,
            passthrough_prompt=prompt,
            message="Passthrough — no LLM configured. Write the primer from passthrough_prompt (host model).",
        )

    out = await llm_adapter.complete(_PROJECT_PROMPT_SYSTEM, user_prompt)
    if not (out or "").strip():
        return _resp(
            passthrough=True,
            passthrough_prompt=prompt,
            message="LLM returned no primer — falling back to passthrough.",
        )
    return _resp(prompt=out.strip())


# --------------------------------------------------------------------------
# enrich_docstrings — write doc comments for undocumented symbols
# --------------------------------------------------------------------------

# Bounds: cap blob reads per call, per-symbol source in the prompt, and the
# total source budget so the prompt stays in one cache-friendly request.
_ENRICH_MAX_SCAN = 2000
_ENRICH_SOURCE_CAP = 1200
_ENRICH_CHAR_BUDGET = 12000

_ENRICH_SYSTEM = (
    "You write clear, idiomatic doc comments for undocumented code symbols. For each "
    "numbered symbol, write a doc comment in that language's conventional format — a "
    "Python triple-quoted docstring, Javadoc/JSDoc/KDoc/PHPDoc `/** */`, rustdoc `///`, "
    "godoc `//`, etc. State what it does, its parameters, and what it returns — concise "
    "and concrete; do not restate the code or pad. Output each as `[n] <symbol>` followed "
    "by the doc comment block, ready to paste directly above the definition."
)


class UndocumentedSymbol(BaseModel):
    index: int
    file: str
    symbol: str
    language: str = ""


class EnrichParams(BaseModel):
    repo_path: str | None = Field(
        default=None, description="Repo path to detect repo_id/branch from. CWD if None."
    )
    repo_id: str | None = Field(default=None, description="Override repo_id (skips detection).")
    branch: str | None = Field(default=None, description="Override branch (skips detection).")
    scope: str | None = Field(
        default=None,
        description="Limit to file paths containing this substring (e.g. 'auth/'). None = whole repo.",
    )
    language: str | None = Field(
        default=None, description="Limit to one language, e.g. 'rust'. None = all."
    )
    max_symbols: int = Field(
        default=15,
        ge=1,
        le=60,
        description="How many undocumented symbols to write doc comments for.",
    )


class EnrichResponse(BaseModel):
    success: bool
    repo_id: str = ""
    branch: str = ""
    documented: int = 0
    undocumented: int = 0
    coverage: float = 0.0
    scanned: int = 0
    unreadable: int = 0
    scan_truncated: bool = False
    symbols: list[UndocumentedSymbol] = Field(default_factory=list)
    answer: str = ""
    truncated: bool = False
    passthrough: bool = False
    passthrough_prompt: str = ""
    message: str = ""


def _symbol_name(row) -> str:
    cn, fn = row["class_name"], row["function_name"]
    if cn and fn:
        return f"{cn}.{fn}"
    return fn or cn or "(anonymous)"


async def enrich_docstrings(
    params: EnrichParams,
    memory_manager: MemoryManager,
    llm_adapter: LLMAdapter,
) -> EnrichResponse:
    import json

    repo_id, branch = await resolve_repo_branch(
        params.repo_id, params.branch, params.repo_path, memory_manager
    )
    if not repo_id or not branch:
        return EnrichResponse(
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
    # Methods are stored as chunk_type='function' (StorageManager._infer_chunk_type
    # derives type from function_name), so 'function' already covers them.
    sql = (
        "SELECT id, file_path, class_name, function_name, language, content_ref "
        "FROM memories WHERE repo_id = ? AND branch_name = ? AND stale = 0 "
        "AND content_type = 'code_snippet' AND chunk_type IN ('function', 'class')"
    )
    args: list = [repo_id, branch]
    if params.language:
        sql += " AND language = ?"
        args.append(params.language)
    if params.scope:
        sql += " AND file_path LIKE ?"
        args.append(f"%{params.scope}%")
    # +1 over the scan cap so we can tell whether more symbols went unscanned.
    sql += " ORDER BY file_path, class_name, function_name LIMIT ?"
    args.append(_ENRICH_MAX_SCAN + 1)

    try:
        rows = conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError as exc:
        logger.debug("enrich_docstrings query failed: %s", exc)
        rows = []

    scan_truncated = len(rows) > _ENRICH_MAX_SCAN
    rows = rows[:_ENRICH_MAX_SCAN]
    if not rows:
        return EnrichResponse(
            success=False,
            repo_id=repo_id,
            branch=branch,
            message=(
                "No indexed function/method/class symbols matched. Index the repo first "
                "(index_repository)."
            ),
        )

    documented = 0
    unreadable = 0
    undoc: list[tuple] = []  # (row, source_text) for symbols with no docstring
    for row in rows:
        try:
            blob = json.loads((storage.base_dir / row["content_ref"]).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            unreadable += 1
            continue
        if (blob.get("docstring") or "").strip():
            documented += 1
        else:
            undoc.append((row, blob.get("text") or ""))

    scanned = documented + len(undoc)
    if scanned == 0:
        # Rows matched but every content blob was unreadable — report that rather
        # than the misleading "everything is already documented".
        return EnrichResponse(
            success=False,
            repo_id=repo_id,
            branch=branch,
            unreadable=unreadable,
            scan_truncated=scan_truncated,
            message=f"Matched {len(rows)} symbol(s) but none had readable content blobs.",
        )
    coverage = round(documented / scanned, 3)

    symbols: list[UndocumentedSymbol] = []
    blocks: list[str] = []
    used = 0
    truncated = len(undoc) > params.max_symbols
    for row, text in undoc[: params.max_symbols]:
        n = len(blocks) + 1
        file = (row["file_path"] or "?").replace("\\", "/")
        sym = _symbol_name(row)
        lang = row["language"] or ""
        block = f"\n[{n}] {file}  {sym}" + (f"  ({lang})" if lang else "") + "\n"
        block += text.strip()[:_ENRICH_SOURCE_CAP] + "\n"
        if blocks and used + len(block) > _ENRICH_CHAR_BUDGET:
            truncated = True
            break
        symbols.append(UndocumentedSymbol(index=n, file=file, symbol=sym, language=lang))
        blocks.append(block)
        used += len(block)

    resp = EnrichResponse(
        success=True,
        repo_id=repo_id,
        branch=branch,
        documented=documented,
        undocumented=len(undoc),
        coverage=coverage,
        scanned=scanned,
        unreadable=unreadable,
        scan_truncated=scan_truncated,
        symbols=symbols,
        truncated=truncated,
    )

    if not symbols:
        resp.message = "Every scanned symbol already has a doc comment — nothing to enrich."
        return resp

    user_prompt = (
        f"Write a doc comment for each undocumented symbol below "
        f"(current coverage: {documented}/{scanned}).\n\n"
        f"=== Undocumented symbols ===\n{''.join(blocks)}\n\n"
        "Return one doc comment per symbol, labelled [n], in the language's conventional format."
    )

    if llm_adapter.is_passthrough():
        resp.passthrough = True
        resp.passthrough_prompt = f"{_ENRICH_SYSTEM}\n\n{user_prompt}"
        resp.message = (
            "Passthrough — no LLM configured. Generate the doc comments from "
            "passthrough_prompt (host model)."
        )
        return resp

    answer = await llm_adapter.complete(_ENRICH_SYSTEM, user_prompt)
    if not (answer or "").strip():
        resp.passthrough = True
        resp.passthrough_prompt = f"{_ENRICH_SYSTEM}\n\n{user_prompt}"
        resp.message = "LLM returned no output — falling back to passthrough."
        return resp
    resp.answer = answer.strip()
    return resp
