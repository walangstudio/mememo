"""Comprehension tools: passthrough-first repo Q&A and architectural overview.

Both turn the indexed graph + chunks into grounding for the host LLM, mirroring
tools/generate_diagram.py and tools/capture.py: when no LLM provider is configured
(the default), return a passthrough_prompt for the host model to answer in chat;
when a provider is configured, answer directly.

- ask:      hybrid-recall the most relevant code chunks for a question and hand them
            back with numbered [n] file:line citations for a grounded, cited answer.
- overview: assemble a deterministic system map (subsystems, key symbols, dependency
            edges, languages) plus the overview Mermaid, then ask the host to name each
            subsystem's responsibility and how the pieces fit together.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

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


async def overview(
    params: OverviewParams,
    memory_manager: MemoryManager,
    llm_adapter: LLMAdapter,
) -> OverviewResponse:
    from ..diagrams import _label, _subsystem, is_empty_diagram, overview_diagram

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
    conn = memory_manager.storage_manager.conn

    file_rows = conn.execute(
        "SELECT file_path, class_name FROM memories "
        "WHERE repo_id = ? AND branch_name = ? AND stale = 0 "
        "AND content_type = 'code_snippet' AND file_path IS NOT NULL",
        (repo_id, branch),
    ).fetchall()
    if not file_rows:
        return OverviewResponse(
            success=False,
            repo_id=repo_id,
            branch=branch,
            message="No indexed code. Index the repo first (index_repository), then retry.",
        )

    subs: dict[str, dict] = {}
    for r in file_rows:
        name = _subsystem(r["file_path"], params.depth)
        d = subs.setdefault(name, {"files": set(), "chunks": 0, "classes": []})
        d["files"].add(r["file_path"])
        d["chunks"] += 1
        cn = r["class_name"]
        if cn and cn not in d["classes"] and len(d["classes"]) < 8:
            d["classes"].append(cn)
    # Cap by the same knob as the diagram so the facts and the Mermaid agree on how
    # many subsystems they show; biggest-by-file-count first. Dropping any sets
    # truncated (below) so the caller isn't told a partial map is complete.
    subs_sorted = sorted(subs.items(), key=lambda kv: len(kv[1]["files"]), reverse=True)
    subsystems = [
        OverviewSubsystem(name=n, files=len(d["files"]), chunks=d["chunks"], classes=d["classes"])
        for n, d in subs_sorted[: params.max_nodes]
    ]
    subsystems_truncated = len(subs_sorted) > params.max_nodes

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
        "SELECT m.file_path, m.class_name, m.function_name, COUNT(*) c "
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

    mermaid = overview_diagram(
        conn, repo_id, branch, max_nodes=params.max_nodes, depth=params.depth
    )
    has_mermaid = not is_empty_diagram(mermaid)
    truncated = subsystems_truncated or "%% truncated" in mermaid

    facts = _overview_facts(subsystems, languages, edge_counts, key_symbols)
    user_prompt = (
        f"=== Codebase structural facts ===\n{facts}\n"
        + (f"\n=== Subsystem dependency diagram (Mermaid) ===\n{mermaid}\n" if has_mermaid else "")
        + "\nWrite the architectural overview."
    )

    mermaid_out = mermaid if has_mermaid else ""

    if llm_adapter.is_passthrough():
        return OverviewResponse(
            success=True,
            repo_id=repo_id,
            branch=branch,
            mermaid=mermaid_out,
            subsystems=subsystems,
            languages=languages,
            key_symbols=key_symbols,
            edge_counts=edge_counts,
            truncated=truncated,
            passthrough=True,
            passthrough_prompt=f"{_OVERVIEW_SYSTEM}\n\n{user_prompt}",
            message=(
                "Passthrough — no LLM configured. Write the overview from "
                "passthrough_prompt (host model)."
            ),
        )

    answer = await llm_adapter.complete(_OVERVIEW_SYSTEM, user_prompt)
    if not (answer or "").strip():
        return OverviewResponse(
            success=True,
            repo_id=repo_id,
            branch=branch,
            mermaid=mermaid_out,
            subsystems=subsystems,
            languages=languages,
            key_symbols=key_symbols,
            edge_counts=edge_counts,
            truncated=truncated,
            passthrough=True,
            passthrough_prompt=f"{_OVERVIEW_SYSTEM}\n\n{user_prompt}",
            message="LLM returned no overview — falling back to passthrough.",
        )

    return OverviewResponse(
        success=True,
        repo_id=repo_id,
        branch=branch,
        mermaid=mermaid_out,
        subsystems=subsystems,
        languages=languages,
        key_symbols=key_symbols,
        edge_counts=edge_counts,
        answer=answer.strip(),
        truncated=truncated,
    )


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
