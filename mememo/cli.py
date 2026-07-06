"""
CLI commands for passive Claude Code hooks.

    python -m mememo capture --hook   # Stop hook: read transcript, auto-capture
    python -m mememo inject --hook    # UserPromptSubmit: inject relevant context
"""

import asyncio
import json
import os
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

    code_types = {"code_snippet", "relationship"}

    persistent: list = []
    code: list = []

    for r in results:
        mem_type = r.memory.content.type
        if mem_type in code_types:
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


def _smart_context_build(results, user_prompt, cfg, srv):
    """Build context using intent-aware adaptive builder with optional skill injection."""
    from .context.adaptive_builder import AdaptiveContextBuilder, BuilderConfig
    from .context.intent_classifier import IntentClassifier

    classifier = IntentClassifier(
        embedder=srv.memory_manager.embedder,
        cache_dir=cfg.storage.base_dir,
    )
    prompt_embedding = srv.memory_manager.embedder.embed_query(user_prompt)
    intent_result = classifier.classify(
        prompt_embedding, confidence_threshold=cfg.hook.intent_confidence_threshold
    )

    # Skill injection (reuse the skill_store from server globals)
    skill_block = None
    skill_tokens_used = 0
    if cfg.hook.skill_injection_enabled and srv.skill_store is not None:
        skills = srv.skill_store.get_skills_for_intent(
            intent_result.intent, cfg.hook.skill_token_budget
        )
        if skills:
            skill_lines = [s.prompt.strip() for s in skills]
            skill_block = "\n".join(skill_lines)
            srv.skill_store.record_use([s.name for s in skills])  # for prune-never-used
            from .utils.token_counter import count_tokens

            skill_tokens_used = count_tokens(skill_block)
            print(
                f"mememo inject: skills={len(skills)} skill_tokens={skill_tokens_used}",
                file=sys.stderr,
            )

    builder_cfg = BuilderConfig(
        base_budget=cfg.hook.inject_token_budget,
        min_budget=cfg.hook.inject_token_budget_min,
        max_budget=cfg.hook.inject_token_budget_max,
        min_similarity=cfg.hook.inject_min_similarity,
    )
    builder = AdaptiveContextBuilder(
        intent=intent_result.intent,
        intent_confidence=intent_result.confidence,
        config=builder_cfg,
    )
    build_result = builder.build(results, skill_tokens_used=skill_tokens_used)

    # Combine skill block + memory block
    combined_block = None
    if skill_block and build_result.block:
        combined_block = f"{skill_block}\n\n{build_result.block}"
    elif skill_block:
        combined_block = skill_block
    elif build_result.block:
        combined_block = build_result.block

    print(
        f"mememo inject: intent={intent_result.intent} confidence={intent_result.confidence:.2f}"
        f" effective_budget={build_result.effective_budget} entries={build_result.entries_included}",
        file=sys.stderr,
    )

    return combined_block, build_result


async def cmd_capture() -> None:
    """Stop hook: read transcript tail and auto-capture memories."""
    from .server import ensure_initialized
    from .tools.capture import capture as capture_impl
    from .tools.schemas import CaptureParams

    raw = sys.stdin.read()
    try:
        hook_data = json.loads(raw)
    except json.JSONDecodeError:
        hook_data = {}

    transcript_path = hook_data.get("transcript_path", "")

    # Load config to get hook settings

    from .types.config import MemoConfig

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

    await ensure_initialized()

    # Re-import after initialization to get populated globals
    import mememo.server as srv

    # Response compression: preprocess transcript and find existing memories
    existing_summaries = None
    if cfg.hook.response_compression_enabled:
        from .context.response_compressor import ResponseCompressor

        compressor = ResponseCompressor()
        original_len = len(text)
        text = compressor.preprocess(text)
        print(
            f"mememo capture: compressed {original_len} -> {len(text)} chars",
            file=sys.stderr,
        )

        # Find existing similar memories to prevent re-extraction
        try:
            from .types.memory import SearchParams

            dedup_results = await srv.memory_manager.search_similar(
                SearchParams(
                    query=text[:500],  # use first 500 chars as query
                    top_k=5,
                    min_similarity=cfg.hook.capture_dedup_similarity,
                    include_stale=False,
                ),
                cwd=hook_data.get("cwd") or None,
            )
            if dedup_results:
                existing_summaries = [
                    f"[{r.memory.content.type}] {r.memory.summary.one_line}" for r in dedup_results
                ]
                print(
                    f"mememo capture: found {len(existing_summaries)} existing similar memories",
                    file=sys.stderr,
                )
        except Exception as e:
            print(f"mememo capture: dedup search failed: {e}", file=sys.stderr)

    # Payload cwd, not ambient: under a shared standalone hookd the process cwd
    # is the daemon's launch dir — captures must land in the caller's repo lane.
    params = CaptureParams(text=text, repo_path=hook_data.get("cwd") or None)
    result = await capture_impl(
        params, srv.memory_manager, srv.llm_adapter, existing_summaries=existing_summaries
    )

    print(
        f"mememo capture: stored={result.stored_count} passthrough={result.passthrough}",
        file=sys.stderr,
    )
    print(json.dumps({"continue": True}))


def cmd_distill() -> None:
    """Sync Stop hook: a cheap gate that, on a complex session, blocks the stop and
    asks the same-session model to distill a reusable skill via ``manage_skill``.

    Kept separate from ``capture`` (which is async — its stdout is discarded by
    Claude Code) because ``decision: block`` only takes effect from a SYNCHRONOUS
    hook. This path does no model load or daemon call: just a config read and a
    transcript scan, so running it synchronously adds negligible latency.
    """
    from .context.skill_distiller import (
        build_distillation_reason,
        count_tool_uses,
        should_distill,
    )
    from .types.config import MemoConfig

    raw = sys.stdin.read()
    try:
        hook_data = json.loads(raw)
    except json.JSONDecodeError:
        hook_data = {}

    cfg = MemoConfig.from_env()
    if not cfg.hook.skill_distill_enabled:
        print(json.dumps({"continue": True}))
        return

    transcript_path = hook_data.get("transcript_path", "")
    n_tools = (
        count_tool_uses(transcript_path, cfg.hook.skill_distill_scan_lines)
        if transcript_path
        else 0
    )
    if should_distill(
        stop_hook_active=bool(hook_data.get("stop_hook_active", False)),
        num_tool_uses=n_tools,
        min_tools=cfg.hook.skill_distill_min_tools,
    ):
        print(f"mememo distill: distilling skill ({n_tools} tools)", file=sys.stderr)
        print(json.dumps({"decision": "block", "reason": build_distillation_reason(n_tools)}))
        return

    print(json.dumps({"continue": True}))


async def cmd_inject() -> None:
    """UserPromptSubmit hook: inject relevant memories as system context."""
    from .server import ensure_initialized
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

    await ensure_initialized()

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
    # Recall the ambient repo lane AND the GLOBAL lane, so cross-project memories
    # (decisions, project notes) surface per prompt — not just the current repo's.
    results = await srv.memory_manager.recall_relevant(
        SearchParams(
            query=user_prompt,
            top_k=20,
            min_similarity=cfg.hook.inject_search_floor,
            include_stale=False,
            hybrid=True,
        ),
        # The payload cwd, not the ambient one: under hookd the process cwd is
        # the daemon's launch dir, which is the wrong lane for every other window.
        cwd=hook_data.get("cwd") or None,
        include_global=cfg.hook.inject_global_lane,
    )

    if cfg.hook.smart_context_enabled:
        block, inject_meta = _smart_context_build(results, user_prompt, cfg, srv)
    else:
        block = _build_context_block(
            results,
            budget=cfg.hook.inject_token_budget,
            min_similarity=cfg.hook.inject_min_similarity,
        )
        inject_meta = None

    if block:
        system_msg = f"Relevant memories from previous sessions:\n{block}"
        token_count = count_tokens(system_msg)
        meta_str = ""
        if inject_meta:
            meta_str = (
                f" intent={inject_meta.intent}({inject_meta.intent_confidence:.2f})"
                f" budget={inject_meta.effective_budget}"
            )
        print(
            f"mememo inject: candidates={len(results)} injected_tokens={token_count}{meta_str}",
            file=sys.stderr,
        )
        print(json.dumps({"continue": True, "systemMessage": system_msg}))
    else:
        print(
            f"mememo inject: candidates={len(results)} nothing above threshold",
            file=sys.stderr,
        )
        print(json.dumps({"continue": True}))


# --- v0.6 PreToolUse hook (T032 / FR-028, FR-029) ---------------------------

_PRE_TOOL_MAX_MEMORIES = 3
_PRE_TOOL_MAX_TOKENS = 300
_PRE_TOOL_BUDGET_S = 1.5  # FR-029: hook must never block; skip if cold embedder takes longer


def _extract_pre_tool_query(tool_name: str, tool_input: dict) -> str | None:
    """Pull a semantic-search query out of the hook payload per tool.

    Returns None when the tool is not one we augment.
    """
    if tool_name == "Grep":
        pattern = tool_input.get("pattern") or ""
        path = tool_input.get("path") or ""
        return (pattern + " " + path).strip() or None
    if tool_name == "Glob":
        return (tool_input.get("pattern") or "").strip() or None
    if tool_name == "Bash":
        cmd = tool_input.get("command") or ""
        # Truncate to keep the embedding generation cheap on noisy inputs.
        return cmd[:200].strip() or None
    return None


def _build_pre_tool_block(results, max_memories: int, max_tokens: int) -> str | None:
    """Format up to N search results into a compact block that fits in
    ``max_tokens``. Returns None when no result is rich enough to be useful.
    """
    from .utils.token_counter import count_tokens

    if not results:
        return None
    lines: list[str] = []
    used = 0
    for r in results[: max_memories * 3]:  # browse a larger window before truncation
        mem = r.memory
        loc = mem.content.file_path or "(no file)"
        if mem.content.line_range:
            loc = f"{loc}:{mem.content.line_range[0]}"
        head = f"- [{mem.content.type}] {mem.summary.one_line.strip()} ({loc})"
        cost = count_tokens(head) + 1  # newline
        if used + cost > max_tokens:
            break
        lines.append(head)
        used += cost
        if len(lines) >= max_memories:
            break
    return "\n".join(lines) if lines else None


async def cmd_pre_tool() -> None:
    """PreToolUse hook: emit related memories alongside Grep/Glob/Bash results.

    The hook NEVER blocks the tool call and NEVER raises — any internal
    failure logs to stderr and emits an empty continue response so Claude
    Code's UX stays untouched (FR-029).
    """
    import sys as _sys

    raw = _sys.stdin.read()
    try:
        hook_data = json.loads(raw)
    except json.JSONDecodeError:
        print(json.dumps({"continue": True}))
        return

    tool_name = hook_data.get("tool_name") or ""
    tool_input = hook_data.get("tool_input") or {}
    query = _extract_pre_tool_query(tool_name, tool_input)
    if not query:
        print(json.dumps({"continue": True}))
        return

    async def _do_search() -> str:
        from .server import ensure_initialized
        from .types.memory import SearchParams

        await ensure_initialized()
        import mememo.server as srv

        results = await srv.memory_manager.search_similar(
            SearchParams(query=query, top_k=10, min_similarity=0.4, include_stale=False),
            cwd=hook_data.get("cwd") or None,
        )
        return _build_pre_tool_block(results, _PRE_TOOL_MAX_MEMORIES, _PRE_TOOL_MAX_TOKENS)

    try:
        block = await asyncio.wait_for(_do_search(), timeout=_PRE_TOOL_BUDGET_S)
    except asyncio.TimeoutError:
        print(
            f"mememo pre-tool: budget {_PRE_TOOL_BUDGET_S}s exceeded "
            "(likely cold embedder); skipping",
            file=_sys.stderr,
        )
        print(json.dumps({"continue": True}))
        return
    except Exception as e:  # never block the tool call
        print(f"mememo pre-tool: error {e}", file=_sys.stderr)
        print(json.dumps({"continue": True}))
        return

    if not block:
        print(json.dumps({"continue": True}))
        return

    payload = {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": f"Related memories ({tool_name}):\n{block}",
        },
    }
    print(json.dumps(payload))


def run_pre_tool():
    asyncio.run(cmd_pre_tool())


def run_capture():
    asyncio.run(cmd_capture())


def run_distill():
    cmd_distill()  # sync: no event loop needed (no awaits)


def _on_inject_deadline() -> None:
    """Hard deadline for the inject hook: emit a no-op continue and kill the
    throwaway hook process. A native model load can't be cancelled cooperatively
    (asyncio.wait_for won't interrupt it), so os._exit is the only reliable way to
    guarantee a UserPromptSubmit hook is never held past budget."""
    sys.stdout.write(json.dumps({"continue": True}) + "\n")
    sys.stdout.flush()
    os._exit(0)


def run_inject():
    """UserPromptSubmit hook entrypoint — best-effort, never blocks the prompt.

    The in-process cold path (embedder load + recall) is normally ~2-3s but can
    stall for tens of seconds under contention (a busy sibling daemon, disk, git),
    and a native model load can't be interrupted cooperatively. A watchdog thread
    hard-exits this throwaway hook process once the budget elapses, injecting
    nothing. Configurable via MEMEMO_INJECT_BUDGET_S.

    The watchdog assumes the stalled work releases the GIL periodically (the
    torch/sentence-transformers load and faiss/sqlite I/O do — verified: a 60s
    contended load is cut to the budget). A pathological pure-Python/GIL-holding
    C loop would starve the watchdog thread, but that is not mememo's cold path.
    """
    import threading

    from .hookclient import bounded_float_env

    # Capped so daemon-timeout (<=10s) + this (<=12s) stays under Claude Code's 30s kill.
    budget = bounded_float_env("MEMEMO_INJECT_BUDGET_S", 10.0, 12.0)

    done = threading.Event()
    completed = False

    def _watchdog() -> None:
        # ponytail: a sub-microsecond boundary race (cmd_inject finishing at ~exactly
        # budget) could still double-emit; the `completed` check shrinks it and it's
        # fail-open anyway — worst case is one skipped injection, never a hang.
        if not done.wait(budget) and not completed:
            _on_inject_deadline()

    threading.Thread(target=_watchdog, daemon=True).start()
    try:
        asyncio.run(cmd_inject())
        completed = True
    finally:
        done.set()


# --- session-start hook (Wave 1B) -------------------------------------------


async def cmd_session_start() -> None:
    """SessionStart hook: recall relevant memories at session open."""
    from .commands.session_start import cmd_session_start as _impl

    await _impl()


def run_session_start():
    asyncio.run(cmd_session_start())


# --- import-md subcommand (Wave 1A) -----------------------------------------


def cmd_import_md(args: list[str]) -> int:
    """Import .md files from a directory as memories."""
    import argparse

    ap = argparse.ArgumentParser(prog="mememo import-md")
    ap.add_argument("dir", help="Directory of .md files to import")
    ap.add_argument("--repo", default=None, help="Repo root path for git context")
    ap.add_argument("--dry-run", action="store_true", help="Parse but do not write")
    ap.add_argument(
        "--allow-secrets",
        action="store_true",
        help="Bypass secret detection (for trusted local md with placeholder creds)",
    )
    ap.add_argument(
        "--whole-file",
        action="store_true",
        help="One memory per file (legacy) instead of one per heading section",
    )
    ns = ap.parse_args(args)

    async def _run() -> int:
        from .importers.markdown_memory import import_markdown_dir
        from .server import initialize_mememo

        await initialize_mememo()
        import mememo.server as srv

        result = await import_markdown_dir(
            path=ns.dir,
            memory_manager=srv.memory_manager,
            repo=ns.repo,
            dry_run=ns.dry_run,
            allow_secrets=ns.allow_secrets,
            per_section=not ns.whole_file,
        )
        label = "(dry run) " if ns.dry_run else ""
        print(
            f"import-md {label}done: imported={result['imported']}"
            f" skipped={result['skipped']} errors={result['errors']}"
        )
        return 0 if result["errors"] == 0 else 1

    return asyncio.run(_run())


def run_import_md():
    import sys

    raise SystemExit(cmd_import_md(sys.argv[1:]))


# --- reindex-identity subcommand (Wave 1C) ----------------------------------


def cmd_reindex_identity(args: list[str]) -> int:
    """Re-derive repo_ids from remote URL and move FAISS dirs to match."""
    import argparse

    ap = argparse.ArgumentParser(prog="mememo reindex-identity")
    ap.add_argument("--dry-run", action="store_true", help="Report changes without applying them")
    ns = ap.parse_args(args)

    from .commands.reindex import reindex_identity
    from .core.storage_manager import StorageManager
    from .types.config import MemoConfig

    cfg = MemoConfig.from_env()
    storage = StorageManager(base_dir=cfg.storage.base_dir)
    base_path = cfg.storage.base_dir / "vector_index"

    report = reindex_identity(storage=storage, base_path=base_path, dry_run=ns.dry_run)

    label = " (dry run)" if report.get("dry_run") else ""
    print(
        f"reindex-identity{label}: moves={report['moves']} conflicts={report['conflicts']}"
        f" noops={report['noops']} skipped={report['skipped']}"
    )
    changed = [e for e in report["manifest"] if not e.get("skipped") and e["old_id"] != e["new_id"]]
    for e in changed:
        print(f"  {e['old_id']} -> {e['new_id']}  {e['repo_path']}  rows={e['row_count']}")
    return 0


def run_reindex_identity():
    import sys

    raise SystemExit(cmd_reindex_identity(sys.argv[1:]))
