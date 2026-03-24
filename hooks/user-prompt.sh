#!/bin/bash
# mememo UserPromptSubmit hook — fires before each user message.
# Retrieves relevant memories and injects them as system context.
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$SCRIPT_DIR/.venv/bin/activate" ] && source "$SCRIPT_DIR/.venv/bin/activate"
exec python -m mememo inject --hook
