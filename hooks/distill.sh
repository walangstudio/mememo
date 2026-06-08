#!/bin/bash
# mememo distill hook — fires (synchronously) when Claude finishes responding.
# Opt-in (MEMEMO_HOOK_SKILL_DISTILL=true): on a session that used enough tools,
# blocks the stop and asks the model to save a reusable skill via manage_skill.
# MUST be synchronous (no `async: true`) — Claude Code discards an async hook's
# stdout, so a decision:block would never take effect. The work is cheap (a
# transcript scan, no model load), so blocking adds negligible latency.
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$SCRIPT_DIR/.venv/bin/activate" ] && source "$SCRIPT_DIR/.venv/bin/activate"
exec python -m mememo distill --hook
