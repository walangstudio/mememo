#!/usr/bin/env bash
# mememo PreToolUse hook (T032 / FR-028, FR-029).
#
# Claude Code invokes this on every Grep / Glob / Bash tool call. We pipe
# the hook payload to `mememo pre-tool --hook` which runs a quick semantic
# search and emits up to 3 related memories totalling at most 300 tokens.
# The hook NEVER blocks the tool call: any failure logs to ~/.mememo/logs
# and we exit 0 with a no-op response.

set -u
exec python -m mememo pre-tool --hook
