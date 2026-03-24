# mememo Passive Hooks

Two Claude Code hooks that make memory capture and context injection fully automatic.

## How they work

**Stop hook** (`stop.sh`) — runs asynchronously after Claude finishes each response. Reads the last N lines of the conversation transcript, extracts memorable facts via LLM, and stores them in mememo. No user action required.

**UserPromptSubmit hook** (`user-prompt.sh`) — runs synchronously before Claude processes each user message. Searches mememo for memories relevant to the prompt and injects them as a system message, within a strict token budget (800 tokens by default). If nothing exceeds the similarity threshold, nothing is injected.

## Setup

### 1. Replace the path placeholder

Edit `hooks.json` and replace `/path/to/mememo` with the absolute path to your mememo checkout:

```bash
# Example (Linux/macOS)
sed -i 's|/path/to/mememo|/home/you/mememo|g' hooks/hooks.json

# Example (Windows Git Bash)
sed -i 's|/path/to/mememo|/c/Users/you/mememo|g' hooks/hooks.json
```

### 2. Set required environment variable

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # or whichever provider you use
```

For capture to work (LLM extraction), you need a configured LLM provider in `mememo/config/providers.yaml`. Without it, the Stop hook runs in passthrough mode (no-op).

### 3. Register the hooks in Claude Code

Copy the contents of `hooks.json` into your Claude Code hooks config (`~/.claude/settings.json` under the `hooks` key), or merge with an existing hooks config.

### 4. Make scripts executable (Linux/macOS only)

```bash
chmod +x hooks/stop.sh hooks/user-prompt.sh
```

### Windows note

On Windows, replace `bash /path/to/mememo/hooks/stop.sh` with the full path using Git Bash or WSL:

```
"command": "C:\\Program Files\\Git\\bin\\bash.exe /c/Users/you/mememo/hooks/stop.sh"
```

Or use WSL:

```
"command": "wsl bash /mnt/c/Users/you/mememo/hooks/stop.sh"
```

## Configuration (environment variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMEMO_HOOK_INJECT_TOKEN_BUDGET` | `800` | Max tokens injected per prompt |
| `MEMEMO_HOOK_INJECT_MIN_SIMILARITY` | `0.25` | Min similarity to include in injected block |
| `MEMEMO_HOOK_INJECT_SEARCH_FLOOR` | `0.2` | Broader search floor (fetch candidates; filtered by MIN_SIMILARITY) |
| `MEMEMO_HOOK_CAPTURE_LINES` | `100` | Transcript tail lines to read |
| `MEMEMO_HOOK_CAPTURE_ENABLED` | `true` | Disable Stop hook |
| `MEMEMO_HOOK_INJECT_ENABLED` | `true` | Disable UserPromptSubmit hook |

## Smoke tests

```bash
# Test capture hook (expects {"continue": true} on stdout)
echo '{"session_id":"test","transcript_path":"/tmp/test.jsonl"}' \
  | python -m mememo capture --hook

# Test inject hook (expects {"continue": true} or {"continue": true, "systemMessage": "..."})
echo '{"session_id":"test","user_prompt":"what decisions did we make about auth?"}' \
  | python -m mememo inject --hook

# Verify MCP server still starts
python -m mememo --version
```
