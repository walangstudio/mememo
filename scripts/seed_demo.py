"""Populate a mememo store with synthetic data for web-UI / performance testing.

The web routes (/memories, /relations, /communities, /snapshots) only read
SQLite — never FAISS and never the JSON content blobs — so synthetic rows with
dummy content_ref values are sufficient to load-test them at scale. This skips
the slow embedder model load + per-chunk encode that real indexing requires.

Usage:
    python scripts/seed_demo.py --memories 10000 --out .mememo-demo

Then:
    $env:MEMEMO_STORAGE_DIR = "<abs path printed below>"
    python -m mememo serve --port 5757
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mememo.core.storage_manager import StorageManager  # noqa: E402
from mememo.types import BranchState, Relation  # noqa: E402

REPO_ID = "demo-repo"
BRANCHES = ["main", "feat/graph", "feat/web"]
CONTENT_TYPES = ["code_snippet", "decision", "analysis", "context", "conversation"]
RISK_GRADES = [None, "MAY_NEED_TESTING", "LIKELY_AFFECTED", "WILL_BREAK"]
REL_TYPES = ["IMPORTS", "CALLS", "EXTENDS", "IMPLEMENTS", "USES", "DECORATED_BY"]
CONFIDENCES = ["EXTRACTED", "INFERRED", "AMBIGUOUS"]


def _sha(i: int) -> str:
    # Deterministic 40-hex SHAs with well-distributed prefixes (sha1 of i) so
    # the SHA-range query in /snapshots and /memories?as_of_sha selects a
    # narrow slice and meaningfully exercises idx_events_commit. A zero-padded
    # int (f"{i:040x}") would give every SHA the same 8-char prefix.
    return hashlib.sha1(str(i).encode()).hexdigest()


def seed(out: Path, n_memories: int, seed_val: int) -> None:
    rng = random.Random(seed_val)
    out.mkdir(parents=True, exist_ok=True)
    storage = StorageManager(base_dir=out)

    # One distinct commit SHA per ~20 memories so the events log has many
    # prefixes (matches the review's >=10k-events hot-path scenario).
    n_commits = max(1, n_memories // 20)
    commit_shas = [_sha(i) for i in range(1, n_commits + 1)]
    base_ts = datetime.now() - timedelta(days=365)

    t0 = time.time()

    # ---- memories (bulk executemany; no helper, no blob files needed) -------
    rows = []
    mem_ids: list[str] = []
    for i in range(n_memories):
        mid = str(uuid4())
        mem_ids.append(mid)
        branch = rng.choice(BRANCHES)
        ctype = rng.choice(CONTENT_TYPES)
        stale = 1 if rng.random() < 0.15 else 0
        created = int((base_ts + timedelta(minutes=i)).timestamp())
        sha = rng.choice(commit_shas)
        rows.append(
            (
                mid,
                REPO_ID,
                "demo",
                str(out),
                branch,
                sha[:12],
                ctype,
                f"src/module_{i % 200}/file_{i}.py",
                rng.randint(1, 40),
                rng.randint(41, 200),
                f"fn_{i}",
                f"Class{i % 50}" if ctype == "code_snippet" else None,
                "python",
                "k",
                f"content/{mid}.json",
                rng.randint(20, 800),
                created,
                created,
                stale,
                ("edited" if stale else None),
            )
        )
    storage.conn.executemany(
        "INSERT INTO memories (id, repo_id, repo_name, repo_path, branch_name, "
        " commit_hash, content_type, file_path, line_start, line_end, "
        " function_name, class_name, language, checksum, content_ref, "
        " token_count, created_at, updated_at, stale, stale_reason) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    storage.conn.commit()

    # ---- v0.4 commit metadata + risk grade (columns added by migration) ----
    updates = [
        (rng.choice(RISK_GRADES[1:]), rng.choice(commit_shas), rng.choice(commit_shas), mid)
        for mid in mem_ids
        if rng.random() < 0.3
    ]
    storage.conn.executemany(
        "UPDATE memories SET risk_grade = ?, created_at_sha = ?, "
        "updated_at_sha = ? WHERE id = ?",
        updates,
    )
    storage.conn.commit()

    # ---- relations (typed edges with communities) --------------------------
    n_rel = int(n_memories * 1.5)
    rels = [
        Relation(
            id=str(uuid4()),
            repo_id=REPO_ID,
            branch=rng.choice(BRANCHES),
            source_memory_id=rng.choice(mem_ids),
            target_memory_id=rng.choice(mem_ids),
            type=rng.choice(REL_TYPES),
            confidence=rng.choice(CONFIDENCES),
            created_at_sha=rng.choice(commit_shas),
            community=rng.randint(0, 19),
        )
        for _ in range(n_rel)
    ]
    for chunk_start in range(0, len(rels), 500):
        storage.insert_relations(rels[chunk_start : chunk_start + 500])

    # ---- memory_events (CREATED + a fraction STALED), bulk insert ----------
    events = []
    for idx, mid in enumerate(mem_ids):
        ev_ts = int((base_ts + timedelta(minutes=idx)).timestamp())
        events.append((rng.choice(commit_shas), mid, "CREATED", "k", "main", ev_ts))
        if rng.random() < 0.2:
            events.append((rng.choice(commit_shas), mid, "STALED", None, "main", ev_ts + 3600))
    storage.conn.executemany(
        "INSERT OR IGNORE INTO memory_events "
        "(commit_sha, memory_id, op, content_sha, branch, ts) "
        "VALUES (?,?,?,?,?,?)",
        events,
    )
    storage.conn.commit()
    n_events = len(events)

    # ---- branch_state ------------------------------------------------------
    for b in BRANCHES:
        storage.upsert_branch_state(
            BranchState(
                repo_id=REPO_ID,
                branch=b,
                last_indexed_sha=commit_shas[-1],
                parent_sha=commit_shas[0],
            )
        )

    dt = time.time() - t0
    abs_out = out.resolve()
    sample = commit_shas[len(commit_shas) // 2]

    print(f"\nSeeded in {dt:.1f}s -> {abs_out}")
    print(f"  memories : {n_memories}")
    print(f"  relations: {n_rel} (20 communities)")
    print(f"  events   : {n_events} across {n_commits} distinct commit SHAs")
    print("\nStart the UI:")
    print(f'  $env:MEMEMO_STORAGE_DIR = "{abs_out}"')
    print("  python -m mememo serve --port 5757")
    print("\nLoad-test the hot paths:")
    print('  hey -n 500 -c 10 "http://127.0.0.1:5757/memories?limit=50"')
    print(
        f"  hey -n 500 -c 10 " f'"http://127.0.0.1:5757/memories?as_of_sha={sample[:8]}&limit=50"'
    )
    print('  hey -n 500 -c 10 "http://127.0.0.1:5757/relations?community=0&limit=50"')
    print(f'  hey -n 200 -c 5  "http://127.0.0.1:5757/snapshots/{sample[:8]}"')
    print("\nVerify the SHA index is used:")
    print(f"  sqlite3 {abs_out / 'mememo.db'}")
    print("  > EXPLAIN QUERY PLAN SELECT ts FROM memory_events")
    print(f"    WHERE commit_sha >= '{sample[:8]}' AND commit_sha < '{sample[:8]}g' " "LIMIT 1;")
    print("  (expect: SEARCH ... USING INDEX idx_events_commit)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed a synthetic mememo store.")
    ap.add_argument("--memories", type=int, default=10000)
    ap.add_argument("--out", type=Path, default=Path(".mememo-demo"))
    ap.add_argument("--seed", type=int, default=42)
    ns = ap.parse_args()
    seed(ns.out, ns.memories, ns.seed)


if __name__ == "__main__":
    main()
