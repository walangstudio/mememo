"""
CLI commands for passive Claude Code hooks.

    python -m mememo capture --hook   # Stop hook: read transcript, auto-capture
    python -m mememo inject --hook    # UserPromptSubmit: inject relevant context
"""

import asyncio
import json
import sys
from pathlib import Path


def _read_jsonl_tail(path: str, max_lines: int) -> str:
    """Read last N lines of a JSONL transcript and extract turn text."""
    p = Path(path)
    if not p.exists():
        return ""

    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = lines[-max_lines:] if len(lines) > max_lines else lines

    turns: list[str] = []
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        role = obj.get("role", "")
        if role not in ("user", "assistant"):
            continue

        content = obj.get("content", "")
        if isinstance(content, str):
            turns.append(f"{role}: {content}")
        elif isinstance(content, list):
            # Content blocks format
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        turns.append(f"{role}: {text}")
                        break

    return "\n\n".join(turns)


def _build_context_block(results, budget: int, min_similarity: float) -> str | None:
    """Build compact context block within token budget."""
    from .utils.token_counter import count_tokens

    CODE_TYPES = {"code_snippet", "relationship"}

    persistent: list = []
    code: list = []

    for r in results:
        mem_type = r.memory.content.type
        if mem_type in CODE_TYPES:
            code.append(r)
        else:
            persistent.append(r)

    lines: list[str] = []
    used_tokens = 0

    # Persistent memories first (higher signal)
    for r in persistent:
        if r.similarity < min_similarity:
            continue

        mem = r.memory
        content_tokens = count_tokens(mem.content.text)
        if content_tokens <= 200:
            text = mem.content.text.strip()
        else:
            text = mem.summary.one_line

        entry = f"- [{mem.content.type}] {text}"
        entry_tokens = count_tokens(entry)
        if used_tokens + entry_tokens > budget:
            continue
        lines.append(entry)
        used_tokens += entry_tokens

    # Code memories: one-line + location only, max 3
    code_added = 0
    for r in code:
        if code_added >= 3:
            break
        if r.similarity < min_similarity:
            continue

        mem = r.memory
        loc = mem.content.file_path or ""
        if mem.content.line_range:
            start, end = mem.content.line_range
            loc = f"{loc}:{start}-{end}"
        entry = (
            f"- [code] {mem.summary.one_line} — {loc}"
            if loc
            else f"- [code] {mem.summary.one_line}"
        )
        entry_tokens = count_tokens(entry)
        if used_tokens + entry_tokens > budget:
            break
        lines.append(entry)
        used_tokens += entry_tokens
        code_added += 1

    if not lines:
        return None

    return "\n".join(lines)


async def cmd_capture() -> None:
    """Stop hook: read transcript tail and auto-capture memories."""
    from .server import initialize_mememo
    from .tools.schemas import CaptureParams
    from .tools.capture import capture as capture_impl

    raw = sys.stdin.read()
    try:
        hook_data = json.loads(raw)
    except json.JSONDecodeError:
        hook_data = {}

    transcript_path = hook_data.get("transcript_path", "")

    # Load config to get hook settings
    from .types.config import MemoConfig
    import os

    cfg = MemoConfig.from_env()

    if not cfg.hook.capture_enabled:
        print(json.dumps({"continue": True}))
        return

    if not transcript_path:
        print("mememo capture: no transcript_path in hook data", file=sys.stderr)
        print(json.dumps({"continue": True}))
        return

    text = _read_jsonl_tail(transcript_path, cfg.hook.capture_transcript_lines)
    if not text.strip():
        print("mememo capture: transcript empty or unreadable", file=sys.stderr)
        print(json.dumps({"continue": True}))
        return

    await initialize_mememo()

    # Re-import after initialization to get populated globals
    import mememo.server as srv

    params = CaptureParams(text=text)
    result = await capture_impl(params, srv.memory_manager, srv.llm_adapter)

    print(
        f"mememo capture: stored={result.stored_count} passthrough={result.passthrough}",
        file=sys.stderr,
    )
    print(json.dumps({"continue": True}))


async def cmd_inject() -> None:
    """UserPromptSubmit hook: inject relevant memories as system context."""
    from .server import initialize_mememo
    from .types.memory import SearchParams

    raw = sys.stdin.read()
    try:
        hook_data = json.loads(raw)
    except json.JSONDecodeError:
        hook_data = {}

    user_prompt = hook_data.get("user_prompt", "")

    from .types.config import MemoConfig

    cfg = MemoConfig.from_env()

    if not cfg.hook.inject_enabled or not user_prompt.strip():
        print(json.dumps({"continue": True}))
        return

    await initialize_mememo()

    import mememo.server as srv
    from .utils.token_counter import count_tokens

    # Lazy TTL cleanup — expire conversation/context memories before searching
    expired = srv.memory_manager.storage_manager.delete_expired_memories(
        ttl_conversation_days=cfg.storage.ttl_conversation_days,
        ttl_context_days=cfg.storage.ttl_context_days,
    )
    for eid in expired:
        srv.memory_manager.vector_index.delete_by_memory_id(eid)
    if expired:
        print(f"mememo inject: expired {len(expired)} memories", file=sys.stderr)

    # Two-stage filtering: broad search floor fetches candidates, inject_min_similarity
    # filters the final block. Keeps high-recall search without polluting the budget.
    search_params = SearchParams(
        query=user_prompt,
        top_k=20,
        min_similarity=cfg.hook.inject_search_floor,
        include_stale=False,
    )
    results = await srv.memory_manager.search_similar(search_params)

    block = _build_context_block(
        results,
        budget=cfg.hook.inject_token_budget,
        min_similarity=cfg.hook.inject_min_similarity,
    )

    if block:
        system_msg = f"Relevant memories from previous sessions:\n{block}"
        token_count = count_tokens(system_msg)
        print(
            f"mememo inject: candidates={len(results)} injected_tokens={token_count}",
            file=sys.stderr,
        )
        print(json.dumps({"continue": True, "systemMessage": system_msg}))
    else:
        print(
            f"mememo inject: candidates={len(results)} nothing above threshold",
            file=sys.stderr,
        )
        print(json.dumps({"continue": True}))


def run_capture():
    asyncio.run(cmd_capture())


def run_inject():
    asyncio.run(cmd_inject())
