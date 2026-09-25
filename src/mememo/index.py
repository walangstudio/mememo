"""SQLite FTS5 index over memory files. Derived and rebuildable; chunks are never deleted, only marked old."""

import hashlib
import os
import shutil
import sqlite3
import time

from . import store

SCHEMA = """
create table if not exists files(path text primary key, lane text, type text, description text,
  mtime real, size int, sha text);
create table if not exists chunks(id integer primary key, sha text unique, path text, lane text, type text,
  card_id text, supersedes text, date text, src text, claim text, body text,
  current int default 1, superseded int default 0, first_seen real, last_seen real);
create index if not exists chunks_path on chunks(path);
create index if not exists chunks_card on chunks(card_id);
create virtual table if not exists fts using fts5(claim, body, content='chunks', content_rowid='id',
  tokenize='porter unicode61');
"""


def db_path():
    return os.path.join(store.home(), "index.db")


def connect(readonly=False):
    if readonly:
        uri = db_path().replace("%", "%25").replace("?", "%3F").replace("#", "%23")
        con = sqlite3.connect(f"file:{uri}?mode=ro", uri=True, timeout=0.2)
        con.execute("pragma query_only=1")
        return con
    os.makedirs(store.home(), exist_ok=True)
    con = sqlite3.connect(db_path(), timeout=5, isolation_level=None)
    con.execute("pragma journal_mode=wal")
    con.executescript(SCHEMA)
    return con


def _snapshot(rel, text, sha, fold_appends=True):
    """Save a version. A pure append replaces the previous snapshot, which it fully contains."""
    d = os.path.join(store.home(), "history", *rel.split("/"))
    os.makedirs(d, exist_ok=True)
    snaps = sorted(os.listdir(d))
    if any(n.endswith(f"-{sha[:8]}.md") for n in snaps):
        return
    if fold_appends and snaps:
        with open(os.path.join(d, snaps[-1]), encoding="utf-8", errors="replace") as f:
            prev = f.read()
        if text.startswith(prev):
            os.remove(os.path.join(d, snaps[-1]))
    name = f"{time.strftime('%Y%m%dT%H%M%S')}{time.time_ns() // 1000 % 1000000:06d}-{sha[:8]}.md"
    with open(os.path.join(d, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _index_file(con, rel, text, now, mtime):
    meta, _ = store.parse_frontmatter(text)
    lane = rel.rsplit("/", 1)[0] if "/" in rel else store.GLOBAL
    type_ = meta.get("type") if meta.get("type") in store.TYPES else "project"
    live = set()
    for c in store.chunks(rel, text):
        body = store.redact(c["body"])
        key = "\0".join(str(c[k]) for k in ("card_id", "supersedes", "date", "src"))
        csha = hashlib.sha256(f"{rel}\0{key}\0{c['claim']}\0{body}".encode()).hexdigest()
        live.add(csha)
        row = con.execute("select id from chunks where sha=?", (csha,)).fetchone()
        if row:
            con.execute("update chunks set current=1, last_seen=?, type=? where id=?", (now, type_, row[0]))
            continue
        date = c["date"] or time.strftime("%Y-%m-%d", time.localtime(mtime))
        cur = con.execute(
            "insert into chunks(sha,path,lane,type,card_id,supersedes,date,src,claim,body,first_seen,last_seen)"
            " values(?,?,?,?,?,?,?,?,?,?,?,?)",
            (csha, rel, lane, type_, c["card_id"], c["supersedes"], date, c["src"], c["claim"], body, now, now),
        )
        con.execute("insert into fts(rowid, claim, body) values(?,?,?)", (cur.lastrowid, c["claim"], body))
    for cid, csha in con.execute("select id, sha from chunks where path=? and current=1", (rel,)).fetchall():
        if csha not in live:
            con.execute("update chunks set current=0, last_seen=? where id=?", (now, cid))
    return lane, type_, meta.get("description", "")


def sweep(con=None):
    """Sync the index with files on disk. Returns number of changed files."""
    own = con is None
    con = con or connect()
    con.execute("begin immediate")  # serializes concurrent sweeps from hooks and the CLI
    try:
        changed = _sweep(con, store.mem_root(), time.time())
        con.execute("commit")
    except BaseException:
        con.execute("rollback")
        raise
    finally:
        if own:
            con.close()
    return changed


def _sweep(con, root, now):
    seen, changed = set(), 0
    known = {r[0]: r[1:] for r in con.execute("select path, mtime, size, sha from files")}
    for dirpath, _, names in os.walk(root):
        for n in names:
            if not n.endswith(".md"):
                continue
            full = os.path.join(dirpath, n)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            seen.add(rel)
            st = os.stat(full)
            old = known.get(rel)
            if old and old[0] == st.st_mtime and old[1] == st.st_size:
                continue
            with open(full, encoding="utf-8", errors="replace") as f:
                text = f.read()
            sha = hashlib.sha256(text.encode()).hexdigest()
            if old and old[2] == sha:
                con.execute("update files set mtime=?, size=? where path=?", (st.st_mtime, st.st_size, rel))
                continue
            _snapshot(rel, text, sha)
            lane, type_, desc = _index_file(con, rel, text, now, st.st_mtime)
            con.execute(
                "insert or replace into files values(?,?,?,?,?,?,?)",
                (rel, lane, type_, desc, st.st_mtime, st.st_size, sha),
            )
            changed += 1
    for rel in set(known) - seen:
        con.execute("update chunks set current=0, last_seen=? where path=?", (now, rel))
        con.execute("delete from files where path=?", (rel,))
        changed += 1
    if changed:
        con.execute(
            "update chunks set superseded = coalesce(card_id in"
            " (select supersedes from chunks where current=1 and supersedes is not null), 0)"
        )
    return changed


def reindex():
    """Rebuild from files. Old (deleted/edited-away) chunks survive only in history/ snapshots."""
    for suffix in ("", "-wal", "-shm"):
        p = db_path() + suffix
        if os.path.exists(p):
            os.remove(p)
    return sweep()


def restore_snapshot(rel, which=-1):
    d = os.path.join(store.home(), "history", *rel.split("/"))
    pick = sorted(os.listdir(d))[which]
    dest = os.path.join(store.mem_root(), *rel.split("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):  # keep the live version, including edits not yet swept
        with open(dest, encoding="utf-8", errors="replace") as f:
            live = f.read()
        _snapshot(rel, live, hashlib.sha256(live.encode()).hexdigest(), fold_appends=False)
    shutil.copyfile(os.path.join(d, pick), dest)
    return pick
