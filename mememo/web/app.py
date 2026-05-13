"""FastAPI app for the v0.6 web UI (T033 / FR-030, FR-031).

Read-only routes:
- GET /repos                       — list of indexed repos
- GET /memories?repo_id&limit      — paginated memory list
- GET /relations?repo_id&branch    — typed-edge list
- GET /communities?repo_id&branch  — community id -> member count
- GET /snapshots/{sha}?repo_id     — set of memory_ids alive at a SHA

Plus static frontend assets served from mememo/web/static/. uvicorn binds
to 127.0.0.1:5757 by default; explicit `--host` is rejected at the CLI
layer so we cannot accidentally listen on a public interface.

The MemoryManager is constructed lazily on first request so importing
this module stays cheap and test-friendly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)


_STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(storage_getter=None) -> FastAPI:
    """Build the FastAPI app.

    Args:
        storage_getter: zero-arg callable returning a StorageManager. The
            production path passes a lazy factory that boots mememo on the
            first call; tests pass a closure over a pre-seeded in-memory
            store.
    """
    app = FastAPI(title="mememo", version="0.6", docs_url=None, redoc_url=None)

    def _storage():
        if storage_getter is None:
            raise HTTPException(503, "storage not initialised")
        return storage_getter()

    @app.get("/repos")
    def list_repos() -> list[dict]:
        storage = _storage()
        rows = storage.conn.execute(
            "SELECT repo_id, repo_name, repo_path, COUNT(*) AS memories "
            "FROM memories GROUP BY repo_id, repo_name, repo_path "
            "ORDER BY memories DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    @app.get("/memories")
    def list_memories(
        repo_id: str | None = None,
        branch: str | None = None,
        as_of_sha: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        """List memories, optionally filtered to those alive at a target SHA.

        When ``as_of_sha`` is given, ``total`` and pagination reflect the
        alive set — not the full table — so the client doesn't have to
        re-paginate after filtering.
        """
        storage = _storage()
        conditions: list[str] = []
        params: list = []
        if repo_id:
            conditions.append("repo_id = ?")
            params.append(repo_id)
        if branch:
            conditions.append("branch_name = ?")
            params.append(branch)
        if as_of_sha:
            from ..types import SHA_PREFIX_PATTERN

            if not SHA_PREFIX_PATTERN.match(as_of_sha):
                raise HTTPException(400, f"as_of_sha must be 4-40 hex chars; got {as_of_sha!r}")
            ts_row = storage.conn.execute(
                "SELECT ts FROM memory_events WHERE commit_sha LIKE ? "
                "ORDER BY ts DESC LIMIT 1",
                (as_of_sha + "%",),
            ).fetchone()
            if ts_row is None:
                return {"total": 0, "offset": offset, "limit": limit, "items": [],
                        "as_of_sha": as_of_sha, "target_ts": None}
            alive = storage.alive_memory_ids_at_ts(
                target_ts=int(ts_row["ts"]), repo_id=repo_id, branch=branch
            )
            if not alive:
                return {"total": 0, "offset": offset, "limit": limit, "items": [],
                        "as_of_sha": as_of_sha, "target_ts": int(ts_row["ts"])}
            alive_list = sorted(alive)
            placeholders = ",".join("?" * len(alive_list))
            conditions.append(f"id IN ({placeholders})")
            params.extend(alive_list)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        total = storage.conn.execute(
            f"SELECT COUNT(*) AS n FROM memories{where}", params
        ).fetchone()["n"]
        rows = storage.conn.execute(
            "SELECT id, repo_id, branch_name, file_path, function_name, class_name, "
            "       content_type, language, stale, risk_grade, created_at, "
            "       created_at_sha, updated_at_sha "
            f"FROM memories{where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return {"total": total, "offset": offset, "limit": limit,
                "items": [dict(r) for r in rows]}

    @app.get("/relations")
    def list_relations(
        repo_id: str | None = None,
        branch: str | None = None,
        community: int | None = None,
        limit: int = Query(default=500, ge=1, le=5000),
    ) -> dict[str, Any]:
        storage = _storage()
        conditions: list[str] = []
        params: list = []
        if repo_id:
            conditions.append("repo_id = ?")
            params.append(repo_id)
        if branch:
            conditions.append("branch = ?")
            params.append(branch)
        if community is not None:
            conditions.append("community = ?")
            params.append(community)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = storage.conn.execute(
            "SELECT id, source_memory_id, target_memory_id, target_symbol, "
            "       type, confidence, community, created_at_sha "
            f"FROM relations{where} LIMIT ?",
            params + [limit],
        ).fetchall()
        return {"count": len(rows), "items": [dict(r) for r in rows]}

    @app.get("/communities")
    def list_communities(
        repo_id: str | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        storage = _storage()
        conditions = ["community IS NOT NULL"]
        params: list = []
        if repo_id:
            conditions.append("repo_id = ?")
            params.append(repo_id)
        if branch:
            conditions.append("branch = ?")
            params.append(branch)
        where = " WHERE " + " AND ".join(conditions)
        rows = storage.conn.execute(
            "SELECT community AS id, COUNT(DISTINCT source_memory_id) AS members, "
            "       COUNT(*) AS edges "
            f"FROM relations{where} GROUP BY community ORDER BY edges DESC",
            params,
        ).fetchall()
        return {"count": len(rows), "items": [dict(r) for r in rows]}

    @app.get("/snapshots/{sha}")
    def snapshot_at_sha(
        sha: str,
        repo_id: str | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        from ..types import SHA_PREFIX_PATTERN

        if not SHA_PREFIX_PATTERN.match(sha):
            raise HTTPException(400, f"sha must be 4-40 hex chars; got {sha!r}")
        storage = _storage()
        # Resolve SHA -> commit ts by looking at the events log; we accept
        # the latest event whose commit_sha starts with the prefix.
        row = storage.conn.execute(
            "SELECT ts FROM memory_events WHERE commit_sha LIKE ? "
            "ORDER BY ts DESC LIMIT 1",
            (sha + "%",),
        ).fetchone()
        if row is None:
            return {"sha": sha, "target_ts": None, "alive_memory_ids": []}
        target_ts = int(row["ts"])
        alive = storage.alive_memory_ids_at_ts(
            target_ts=target_ts, repo_id=repo_id, branch=branch
        )
        return {
            "sha": sha, "target_ts": target_ts,
            "alive_memory_ids": sorted(alive),
        }

    # Static frontend assets — mounted at root so / serves index.html.
    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

        @app.get("/", include_in_schema=False)
        def root() -> FileResponse:
            return FileResponse(_STATIC_DIR / "index.html")

    return app


def run(host: str = "127.0.0.1", port: int = 5757) -> None:
    """Boot uvicorn against the production app. CLI entrypoint."""
    import uvicorn

    if host not in ("127.0.0.1", "localhost"):
        raise ValueError(
            "mememo serve binds to localhost only; refusing to expose on a "
            "public interface (FR-030 / constitution)"
        )

    def _factory():
        # Lazy import so the cost only hits when the server actually boots.
        from ..server import initialize_mememo

        import asyncio as _asyncio

        loop = _asyncio.new_event_loop()
        try:
            loop.run_until_complete(initialize_mememo())
        finally:
            loop.close()
        import mememo.server as srv

        return srv.memory_manager.storage_manager

    storage = _factory()
    app = create_app(storage_getter=lambda: storage)
    uvicorn.run(app, host=host, port=port, log_level="info")
