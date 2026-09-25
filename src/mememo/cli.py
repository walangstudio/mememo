"""mememo command line."""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time

from . import __version__, index, search, store


def _lane(arg):
    return store.GLOBAL if arg in (None, "global") else store.lane_for(os.getcwd()) if arg == "here" else arg


def cmd_add(a):
    body = sys.stdin.read() if a.body == "-" else a.body or ""
    try:
        cid, path = store.add_card(_lane(a.lane), a.topic, a.type, a.claim, body, a.supersedes, a.src)
    except (ValueError, TimeoutError) as e:
        sys.exit(f"mememo: {e}")
    print(f"saved {cid} -> {path}")
    try:
        index.sweep()
    except sqlite3.Error as e:  # the card is on disk; the next sweep indexes it
        print(f"mememo: saved but not indexed yet ({e})", file=sys.stderr)


def cmd_search(a):
    con = index.connect()
    index.sweep(con)
    hits = search.search(con, a.query, store.lane_for(os.getcwd()), k=a.k, include_old=a.all)
    if not hits:
        print("no matching memory")
    for h in hits:
        print(f"{search._label(h)} {h['claim']}")
        if a.full and h["body"]:
            print("    " + h["body"].replace("\n", "\n    "))


def cmd_show(a):
    con = index.connect()
    index.sweep(con)
    if a.ref.startswith("m-"):
        rows = con.execute(
            "select path, claim, body, date, current, superseded from chunks where card_id=?"
            " order by current desc, last_seen desc, id",
            (a.ref,),
        ).fetchall()
        if not rows:
            sys.exit(f"mememo: no card {a.ref}")
        rows = [r for r in rows if r[4]] or rows[:1]
        path, claim, _, date, cur, sup = rows[0]
        state = "SUPERSEDED" if sup else "" if cur else "DELETED"
        print(f"{a.ref} {date} {path} {state}\n## {claim}\n" + "\n\n".join(r[2] for r in rows if r[2]))
        return
    full = os.path.join(store.mem_root(), *a.ref.split("/"))
    if not os.path.exists(full):
        sys.exit(f"mememo: no file {a.ref}")
    with open(full, encoding="utf-8") as f:
        sys.stdout.write(f.read())


def cmd_sweep(a):
    print(f"{index.reindex() if a.rebuild else index.sweep()} file(s) changed")


def cmd_status(a):
    con = index.connect()
    index.sweep(con)
    q = lambda s: con.execute(s).fetchone()[0]  # noqa: E731
    print(f"mememo {__version__}  home {store.home()}")
    print(
        f"files {q('select count(*) from files')}  chunks live {q('select count(*) from chunks where current=1 and superseded=0')}"
        f"  superseded {q('select count(*) from chunks where superseded=1')}  old {q('select count(*) from chunks where current=0')}"
    )
    print(f"lane here: {store.lane_for(os.getcwd())}  sqlite {sqlite3.sqlite_version}")
    log = os.path.join(store.home(), "hook.log")
    if os.path.exists(log):
        with open(log, encoding="utf-8") as f:
            tail = f.readlines()[-200:]
        ms = sorted(int(line.rsplit("ms=", 1)[1]) for line in tail if "UserPromptSubmit" in line and "ms=" in line)
        errs = [line for line in tail if " ERROR " in line]
        if ms:
            print(
                f"prompt hook: n={len(ms)} p50 {ms[len(ms) // 2]}ms p95 {ms[int(len(ms) * .95) - 1 if len(ms) > 1 else 0]}ms"
            )
        for e in errs[-3:]:
            print("hook error:", e.strip())


def cmd_import(a):
    src = os.path.expanduser(a.src)
    dest = os.path.join(store.mem_root(), *_lane(a.lane).split("/"))
    os.makedirs(dest, exist_ok=True)
    n = 0
    for name in sorted(os.listdir(src)):
        if not name.endswith(".md") or name == "MEMORY.md":
            continue
        target = os.path.join(dest, name)
        if not os.path.exists(target):
            shutil.copy2(os.path.join(src, name), target)
            n += 1
    index.sweep()
    print(f"imported {n} file(s) into {dest}")


def cmd_restore(a):
    print(f"restored {a.path} from {index.restore_snapshot(a.path, a.n)}")
    index.sweep()


def hook_command():
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
    if os.name == "nt" and os.path.exists(pyw):
        py = pyw
    return f'"{py}" -I -S "{os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.py")}"'


def hooks_config():
    cmd = hook_command()
    h = lambda **kw: [{**kw, "hooks": [{"type": "command", "command": cmd, "timeout": 10}]}]  # noqa: E731
    stop = [{"hooks": [{"type": "command", "command": cmd, "timeout": 60, "async": True}]}]
    return {"SessionStart": h(matcher="startup|clear|compact"), "UserPromptSubmit": h(), "Stop": stop}


def cmd_install(a):
    path = os.path.expanduser(a.settings)
    cfg = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        shutil.copy2(path, f"{path}.mememo-backup-{time.strftime('%Y%m%d%H%M%S')}")
    hooks = cfg.setdefault("hooks", {})
    for event, entries in hooks_config().items():
        kept = [
            e
            for e in hooks.get(event, [])
            if not any(
                "mememo" in h.get("command", "") and "hook.py" in h.get("command", "") for h in e.get("hooks", [])
            )
        ]
        hooks[event] = kept + entries
    if not a.keep_auto_memory:
        cfg["autoMemoryEnabled"] = False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    print(f"installed mememo hooks into {path}" + ("" if a.keep_auto_memory else " (autoMemoryEnabled=false)"))


def main(argv=None):
    p = argparse.ArgumentParser(prog="mememo", description="Unlimited local memory for coding agents.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add", help="save a memory card")
    s.add_argument("--claim", required=True)
    s.add_argument("--body", help='card body; "-" reads stdin')
    s.add_argument("--type", default="project", choices=store.TYPES)
    s.add_argument("--topic", default="notes")
    s.add_argument("--lane", help="global (default), here, or an explicit lane path")
    s.add_argument("--supersedes")
    s.add_argument("--src")
    s.set_defaults(fn=cmd_add)
    s = sub.add_parser("search", help="search memory")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=8)
    s.add_argument("--full", action="store_true", help="print bodies")
    s.add_argument("--all", action="store_true", help="include superseded and deleted")
    s.set_defaults(fn=cmd_search)
    s = sub.add_parser("show", help="print a card (m-...) or memory file path")
    s.add_argument("ref")
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("sweep", help="sync index with files")
    s.add_argument("--rebuild", action="store_true")
    s.set_defaults(fn=cmd_sweep)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    s = sub.add_parser("import", help="copy markdown memory files (e.g. a Claude memory dir)")
    s.add_argument("src")
    s.add_argument("--lane")
    s.set_defaults(fn=cmd_import)
    s = sub.add_parser("restore", help="restore a file from history")
    s.add_argument("path")
    s.add_argument("-n", type=int, default=-1, help="snapshot index, default latest")
    s.set_defaults(fn=cmd_restore)
    s = sub.add_parser("install", help="add hooks to a Claude Code settings file")
    s.add_argument("--settings", default="~/.claude/settings.json")
    s.add_argument("--keep-auto-memory", action="store_true")
    s.set_defaults(fn=cmd_install)
    for stream in (sys.stdin, sys.stdout, sys.stderr):  # Windows pipes default to cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
