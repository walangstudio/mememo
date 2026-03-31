"""
capture tool - Passive memory capture via LLM extraction.

Takes raw text (conversation snippet, session notes, observations) and uses
a configured LLM to extract memorable facts, storing each as the appropriate
memory type automatically.

When no LLM is configured (passthrough mode), returns a prompt the calling
model can use to self-extract by calling store_memory directly.
"""

import json
import logging
from typing import TYPE_CHECKING

from ..types.memory import CreateMemoryParams, MemoryRelationships, SearchParams
from .schemas import CaptureParams, CaptureResponse, ExtractedMemory

if TYPE_CHECKING:
    from ..core.llm_adapter import LLMAdapter
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

_EXTRACT_TYPES = {"decision", "analysis", "context", "conversation", "summary"}

_SYSTEM_PROMPT = """\
You are a memory extraction assistant. Given a text, extract all facts worth \
storing for future reference.

Return a JSON array of objects with these fields:
  type    — one of: decision, analysis, context, conversation, summary
  content — the extracted fact, self-contained and clearly written
  tags    — list of relevant short tags (can be empty)

Rules:
- Only extract genuinely useful, non-obvious facts
- Each item must be self-contained (readable without the source text)
- Skip ephemeral, trivial, or already-obvious information
- For decisions: capture what was chosen and why, not just that a choice was made
- Prefer fewer high-quality extractions over many mediocre ones
- Return [] if nothing is worth storing

Return ONLY valid JSON — no prose, no markdown fences.\
"""

_PASSTHROUGH_TEMPLATE = """\
No LLM is configured for mememo (passthrough mode). Extract memorable facts \
from the text below and store each one by calling store_memory with the \
appropriate type (decision, analysis, context, conversation, or summary).

Only store facts that are genuinely useful for future sessions — skip \
ephemeral details. For decisions, capture what was chosen and why.

Text to analyze:
---
{text}
---
{hint_line}\
"""


def _build_passthrough_prompt(text: str, hint: str | None) -> str:
    hint_line = f"\nHint: {hint}" if hint else ""
    return _PASSTHROUGH_TEMPLATE.format(text=text, hint_line=hint_line)


def _parse_extracted(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    logger.warning("Failed to parse LLM extraction response as JSON")
    return []


async def capture(
    params: CaptureParams,
    memory_manager: "MemoryManager",
    llm_adapter: "LLMAdapter",
    existing_summaries: list[str] | None = None,
) -> CaptureResponse:
    if llm_adapter.is_passthrough():
        return CaptureResponse(
            success=True,
            extracted=[],
            stored_count=0,
            message="Passthrough mode — no LLM configured. Use passthrough_prompt to self-extract.",
            passthrough=True,
            passthrough_prompt=_build_passthrough_prompt(params.text, params.hint),
        )

    user_prompt = params.text
    if params.hint:
        user_prompt = f"Hint: {params.hint}\n\n{params.text}"

    system_prompt = _SYSTEM_PROMPT
    if existing_summaries:
        from ..context.response_compressor import ResponseCompressor

        system_prompt = ResponseCompressor.build_enhanced_prompt(system_prompt, existing_summaries)

    raw = await llm_adapter.complete(system_prompt, user_prompt)
    if raw is None:
        return CaptureResponse(
            success=True,
            extracted=[],
            stored_count=0,
            message="LLM call failed — falling back to passthrough.",
            passthrough=True,
            passthrough_prompt=_build_passthrough_prompt(params.text, params.hint),
        )

    items = _parse_extracted(raw)
    extracted: list[ExtractedMemory] = []

    for item in items:
        mem_type = item.get("type", "context")
        if mem_type not in _EXTRACT_TYPES:
            mem_type = "context"
        content = str(item.get("content", "")).strip()
        tags = [str(t) for t in item.get("tags", []) if t]
        if not content:
            continue

        # Dedup check — fail open so a search error never silently drops a memory
        try:
            dupes = await memory_manager.search_similar(
                SearchParams(query=content, top_k=1, min_similarity=0.97),
                cwd=params.repo_path,
            )
            if dupes:
                logger.debug("Skipping duplicate memory (similarity=%.3f)", dupes[0].similarity)
                continue
        except Exception as e:
            logger.debug("Dedup check failed, proceeding to store: %s", e)

        memory_id = ""
        try:
            create_params = CreateMemoryParams(
                content=content,
                type=mem_type,
                tags=tags or None,
                relationships=MemoryRelationships(),
            )
            memory = await memory_manager.create_memory(create_params, cwd=params.repo_path)
            memory_id = memory.id
        except Exception as e:
            logger.warning("Failed to store extracted memory: %s", e)

        extracted.append(
            ExtractedMemory(type=mem_type, content=content, tags=tags, memory_id=memory_id)
        )

    stored_count = sum(1 for e in extracted if e.memory_id)
    return CaptureResponse(
        success=True,
        extracted=extracted,
        stored_count=stored_count,
        message=f"Extracted {len(extracted)} memories, stored {stored_count}",
        passthrough=False,
    )
