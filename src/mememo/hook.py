"""Agent hook entrypoint. Run as: python -I -S <this file>. Stdlib only, always exits 0 (fail open)."""

import json
import os
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # after stdlib: backports can't shadow it

from mememo import index, search, store  # noqa: E402


def _seen_file(sid):
    return os.path.join(store.home(), "sessions", "".join(c for c in sid if c.isalnum() or c in "-_") + ".txt")


def _prompt(data):
    lane = store.lane_for(data.get("cwd"))
    con = index.connect(readonly=True)
    hits = search.search(con, data.get("prompt") or "", lane)
    con.close()
    sid = data.get("session_id") or ""
    if sid and hits:
        seen_path = _seen_file(sid)
        seen = set()
        if os.path.exists(seen_path):
            with open(seen_path) as f:
                seen = set(f.read().split())
        hits = [h for h in hits if h["id"] not in seen]
        os.makedirs(os.path.dirname(seen_path), exist_ok=True)
        with open(seen_path, "a") as f:
            f.write("".join(f"{h['id']}\n" for h in hits))
    return search.recall_block(hits), len(hits)


def _session_start(data):
    sid = data.get("session_id") or ""
    if sid and os.path.exists(_seen_file(sid)) and data.get("source") in ("clear", "compact"):
        os.remove(_seen_file(sid))
    con = index.connect()
    index.sweep(con)
    ctx = search.digest(con, store.lane_for(data.get("cwd")))
    con.close()
    return ctx, 0


def main():
    t = time.perf_counter()
    data = json.loads(sys.stdin.buffer.read() or b"{}")
    event = data.get("hook_event_name", "")
    ctx, n = "", 0
    if event == "UserPromptSubmit":
        if (data.get("prompt") or "").lstrip().startswith("/"):
            return
        ctx, n = _prompt(data)
    elif event == "SessionStart":
        ctx, n = _session_start(data)
    elif event == "Stop":
        index.sweep()
    if ctx:
        out = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": ctx}}
        sys.stdout.buffer.write(json.dumps(out).encode())
    _log(f"{event} hits={n} chars={len(ctx)} ms={(time.perf_counter() - t) * 1000:.0f}")


def _log(line):
    try:
        with open(os.path.join(store.home(), "hook.log"), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")
    except OSError:
        pass


if __name__ == "__main__":
    if len(sys.argv) > 1:  # CLI mode: same fast interpreter path the hooks use, no .exe shim
        from mememo import cli

        cli.main(sys.argv[1:])
        sys.exit(0)
    try:
        main()
    except BaseException as e:  # never block the agent
        _log(f"ERROR {type(e).__name__}: {e}")
    sys.exit(0)
