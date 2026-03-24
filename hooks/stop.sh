#!/bin/bash
# mememo Stop hook — fires when Claude finishes responding.
# Reads transcript and auto-captures memorable facts.
# Set async: true in hooks.json so it doesn't block Claude.
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$SCRIPT_DIR/.venv/bin/activate" ] && source "$SCRIPT_DIR/.venv/bin/activate"
exec python -m mememo capture --hook
