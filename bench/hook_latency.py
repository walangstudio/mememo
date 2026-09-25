"""Spawn the hook the way an agent does and report p50/p95. Uses MEMEMO_HOME; run `mememo sweep` first."""

import json
import os
import statistics
import subprocess
import sys
import time

HOOK = os.path.join(os.path.dirname(__file__), "..", "src", "mememo", "hook.py")
PROMPTS = [
    "the cmd window keeps flashing when hooks run",
    "can I merge this PR now?",
    "portable-pty ConPTY hangs on windows",
    "write a haiku about autumn leaves",
    "how do I land three dependent PRs without the top ones closing",
]


def main(n=30):
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    py = pyw if os.path.exists(pyw) else py
    for event in ("UserPromptSubmit", "SessionStart"):
        lat, size = [], 0
        for i in range(n + 2):
            payload = {
                "hook_event_name": event,
                "prompt": PROMPTS[i % len(PROMPTS)],
                "cwd": os.getcwd(),
                "session_id": f"bench-{time.time_ns()}",
                "source": "startup",
            }
            t = time.perf_counter()
            r = subprocess.run(
                [py, "-I", "-S", HOOK],
                input=json.dumps(payload).encode(),
                capture_output=True,
                env=os.environ,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if i >= 2:
                lat.append((time.perf_counter() - t) * 1000)
                size = max(size, len(r.stdout))
            if r.stdout:
                json.loads(r.stdout)
        lat.sort()
        print(
            f"{event:17} p50 {statistics.median(lat):6.1f}ms  p95 {lat[int(.95 * len(lat)) - 1]:6.1f}ms"
            f"  max {lat[-1]:6.1f}ms  max-output {size}B"
        )


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
