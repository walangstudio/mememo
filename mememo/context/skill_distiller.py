"""Autonomous skill distillation for the Stop hook (Hermes-style closed loop).

When a session does real work (>= a tool-call threshold), the Stop hook hands the
*same-session* model a short instruction — via the hook's ``decision: block`` /
``reason`` channel — to reflect and, if the session demonstrated a reusable
technique, save it as a skill through the existing ``manage_skill`` tool. The
skill lands in ``SkillStore`` and is injected into future sessions by intent.

Passthrough-native: the host model does the distillation (no LLM/API call here),
mirroring the capture/diagram passthrough pattern. This module is the pure,
testable core; ``cli.cmd_distill`` wires it into the (synchronous) Stop hook.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

# Intents the IntentClassifier routes on (see context/intent_classifier.py
# INTENT_PHRASES). A distilled skill must carry one of these as its ``intent`` or
# the intent-based injection will never surface it.
VALID_INTENTS = ("coding", "debugging", "architecture", "testing", "review", "general")


def count_tool_uses(transcript_path: str, max_lines: int) -> int:
    """Count ``tool_use`` content blocks in the last ``max_lines`` of a transcript.

    Robust to both the flat ``{role, content:[...]}`` shape and the nested
    ``{message: {content:[...]}}`` shape Claude Code writes. Unreadable or
    malformed lines are skipped — the count is a gate signal, not an exact metric.
    """
    p = Path(transcript_path)
    if not p.exists():
        return 0

    # Stream the tail instead of reading the whole file: a long session's
    # transcript can be hundreds of MB, and this runs on the synchronous hook.
    try:
        with p.open(encoding="utf-8", errors="replace") as fh:
            tail = collections.deque(fh, maxlen=max_lines)
    except OSError:
        return 0

    count = 0
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        content = obj.get("content")
        if not isinstance(content, list):
            msg = obj.get("message")
            content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                count += 1
    return count


def should_distill(*, stop_hook_active: bool, num_tool_uses: int, min_tools: int) -> bool:
    """Whether a (already-enabled) session is worth a skill-distillation pass.

    The caller gates on the opt-in flag; this decides on session shape only.
    ``stop_hook_active`` is True when Claude is *already* continuing because of a
    prior Stop-hook block — gating on it prevents an infinite distill→stop→distill
    loop (the model gets exactly one distillation pass per stop attempt).
    """
    if stop_hook_active:
        return False
    return num_tool_uses >= min_tools


def build_distillation_reason(num_tool_uses: int) -> str:
    """The instruction delivered to the same-session model via the hook's reason.

    No transcript is embedded — the model that receives this already has the full
    session in context; it just needs the nudge and the storage contract.
    """
    intents = ", ".join(VALID_INTENTS)
    return (
        f"This session used {num_tool_uses} tools. Before finishing, do a quick "
        "self-review for reuse. If it demonstrated a GENERALIZABLE technique or "
        "workflow a future session could reuse (not facts specific to this repo's "
        "values), save ONE skill: call the mememo `manage_skill` tool with "
        "action='create', a short kebab-case `name`, an `intent` (one of: "
        f"{intents}), a `prompt` holding the reusable steps/commands/gotchas written "
        "generically, and relevant `tags`. If nothing is genuinely reusable, create "
        "nothing. Save at most one high-quality skill, then conclude."
    )
