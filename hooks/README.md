# mememo Passive Hooks

Four Claude Code hooks that make memory capture, recall, and self-learning fully automatic.

## How they work

**UserPromptSubmit** (`inject --hook`), runs before Claude processes each user message. Searches mememo for memories relevant to the prompt and injects them as a system message, within a token budget (800 by default). Nothing is injected if no result exceeds the similarity threshold.

**PreToolUse** (`pre-tool --hook`, matcher `Grep|Glob|Bash`), runs before those tools and surfaces related memories alongside the tool result.

**SessionStart** (`session-start --hook`), recalls memories relevant to the session at open.

**Stop**: `capture --hook` runs asynchronously after every response, extracting memorable facts via LLM and storing them. Requires an LLM provider configured (e.g. `ANTHROPIC_API_KEY`, see `mememo/config/providers.yaml`); without one it runs in passthrough mode (no-op). `distill --hook` runs synchronously afterward; opt-in (`MEMEMO_HOOK_SKILL_DISTILL=true`), it asks the model to save a reusable skill after a session that used enough tool calls (must stay synchronous, `decision:block` has no effect from an async hook).

## Setup

`hooks.json` invokes the bare `mememo` command, no path substitution needed. Requires the `mememo` package on `PATH` (see the main [README](../README.md#-installation)):

```bash
pip install mememo   # or: pip install -e . / uv tool install . from a clone
```

**Installed as a Claude Code plugin** (`/plugin install mememo@walangstudio`): these hooks are wired automatically.

**Any other Claude Code install**: copy the contents of `hooks.json` into `~/.claude/settings.json` under the `hooks` key (merge with any existing hooks config).

## Configuration (environment variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMEMO_HOOK_INJECT_TOKEN_BUDGET` | `800` | Max tokens injected per prompt |
| `MEMEMO_HOOK_INJECT_MIN_SIMILARITY` | `0.25` | Min similarity to include in injected block |
| `MEMEMO_HOOK_INJECT_SEARCH_FLOOR` | `0.2` | Broader search floor (fetch candidates; filtered by MIN_SIMILARITY) |
| `MEMEMO_HOOK_CAPTURE_LINES` | `100` | Transcript tail lines to read |
| `MEMEMO_HOOK_CAPTURE_ENABLED` | `true` | Disable the Stop capture hook |
| `MEMEMO_HOOK_INJECT_ENABLED` | `true` | Disable the UserPromptSubmit hook |
| `MEMEMO_HOOK_SKILL_DISTILL` | `false` | Enable the Stop skill-distillation hook |

## Smoke tests

```bash
# Test capture hook (expects {"continue": true} on stdout)
echo '{"session_id":"test","transcript_path":"/tmp/test.jsonl"}' | mememo capture --hook

# Test inject hook (expects {"continue": true} or {"continue": true, "systemMessage": "..."})
echo '{"session_id":"test","user_prompt":"what decisions did we make about auth?"}' | mememo inject --hook

# Verify the MCP server still starts
mememo --version
```
