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


def _build_fts_query(q: str) -> str:
    """Turn a user search box query into a safe FTS5 ``MATCH`` expression.

    Strips FTS operators (-, ", *, NEAR, AND, OR, …) by reducing each token to
    word characters, then appends ``*`` for prefix matching so ``audit``
    matches ``audit_tail``. Multi-token queries become AND-ed prefix matches.
    Returns ``""`` if nothing usable remains — caller falls back to LIKE only.
    """
    import re

    tokens = []
    for tok in q.split():
        cleaned = re.sub(r"[^A-Za-z0-9_]", "", tok)
        if cleaned:
            tokens.append(cleaned + "*")
    return " ".join(tokens)


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
        q: str | None = None,
        content_type: str | None = None,
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
        if content_type:
            conditions.append("content_type = ?")
            params.append(content_type)
        if q:
            # Literal substring search over the human-meaningful columns. Escape
            # LIKE metacharacters (\ % _) so e.g. 'audit_tail' matches literally.
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            # Also OR through FTS5 over body text so decisions/docs/context
            # notes (which have NULL file/fn/class) are findable by content.
            fts_q = _build_fts_query(q)
            if fts_q:
                conditions.append(
                    "(file_path LIKE ? ESCAPE '\\' "
                    "OR function_name LIKE ? ESCAPE '\\' "
                    "OR class_name LIKE ? ESCAPE '\\' "
                    "OR id IN (SELECT memory_id FROM memories_fts WHERE memories_fts MATCH ?))"
                )
                params.extend([like, like, like, fts_q])
            else:
                conditions.append(
                    "(file_path LIKE ? ESCAPE '\\' "
                    "OR function_name LIKE ? ESCAPE '\\' "
                    "OR class_name LIKE ? ESCAPE '\\')"
                )
                params.extend([like, like, like])
        if as_of_sha:
            from ..types import SHA_PREFIX_PATTERN

            if not SHA_PREFIX_PATTERN.match(as_of_sha):
                raise HTTPException(400, f"as_of_sha must be 4-40 hex chars; got {as_of_sha!r}")
            # Use range query so SQLite's BINARY index on commit_sha is hit;
            # parameterised LIKE 'prefix%' can't be statically rewritten to a range.
            if len(as_of_sha) == 40:
                ts_row = storage.conn.execute(
                    "SELECT ts FROM memory_events WHERE commit_sha = ? ORDER BY ts DESC LIMIT 1",
                    (as_of_sha,),
                ).fetchone()
            else:
                upper = as_of_sha + "g"  # 'g' > any hex digit in BINARY collation
                ts_row = storage.conn.execute(
                    "SELECT ts FROM memory_events "
                    "WHERE commit_sha >= ? AND commit_sha < ? "
                    "ORDER BY ts DESC LIMIT 1",
                    (as_of_sha, upper),
                ).fetchone()
            if ts_row is None:
                return {
                    "total": 0,
                    "offset": offset,
                    "limit": limit,
                    "items": [],
                    "as_of_sha": as_of_sha,
                    "target_ts": None,
                }
            alive = storage.alive_memory_ids_at_ts(
                target_ts=int(ts_row["ts"]), repo_id=repo_id, branch=branch
            )
            if not alive:
                return {
                    "total": 0,
                    "offset": offset,
                    "limit": limit,
                    "items": [],
                    "as_of_sha": as_of_sha,
                    "target_ts": int(ts_row["ts"]),
                }
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
        return {"total": total, "offset": offset, "limit": limit, "items": [dict(r) for r in rows]}

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
            conditions.append("r.repo_id = ?")
            params.append(repo_id)
        if branch:
            conditions.append("r.branch = ?")
            params.append(branch)
        if community is not None:
            conditions.append("r.community = ?")
            params.append(community)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        # LEFT JOIN memories on both endpoints so the client can label nodes
        # (file_path + class.fn) instead of showing raw UUIDs. LEFT keeps edges
        # whose target is an unresolved symbol (target_memory_id IS NULL).
        rows = storage.conn.execute(
            "SELECT r.id, r.source_memory_id, r.target_memory_id, r.target_symbol, "
            "       r.type, r.confidence, r.community, r.created_at_sha, "
            "       sm.file_path AS source_file, sm.class_name AS source_class, "
            "       sm.function_name AS source_fn, "
            "       tm.file_path AS target_file, tm.class_name AS target_class, "
            "       tm.function_name AS target_fn "
            "FROM relations r "
            "LEFT JOIN memories sm ON sm.id = r.source_memory_id "
            "LEFT JOIN memories tm ON tm.id = r.target_memory_id "
            f"{where} LIMIT ?",
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
        # Resolve SHA -> commit ts via the events log. Range query so the
        # idx_events_commit B-tree is used; parameterised LIKE 'prefix%'
        # cannot be statically rewritten to a range by SQLite.
        if len(sha) == 40:
            row = storage.conn.execute(
                "SELECT ts FROM memory_events WHERE commit_sha = ? ORDER BY ts DESC LIMIT 1",
                (sha,),
            ).fetchone()
        else:
            row = storage.conn.execute(
                "SELECT ts FROM memory_events "
                "WHERE commit_sha >= ? AND commit_sha < ? "
                "ORDER BY ts DESC LIMIT 1",
                (sha, sha + "g"),
            ).fetchone()
        if row is None:
            return {"sha": sha, "target_ts": None, "alive_memory_ids": []}
        target_ts = int(row["ts"])
        alive = storage.alive_memory_ids_at_ts(target_ts=target_ts, repo_id=repo_id, branch=branch)
        return {
            "sha": sha,
            "target_ts": target_ts,
            "alive_memory_ids": sorted(alive),
        }

    _diagram_types = {"class", "call", "module"}
    _llm_diagram_types = {"sequence", "usecase", "state", "erd"}

    @app.get("/diagram")
    def get_diagram(
        type: str,
        scope: str | None = None,
        repo_id: str | None = None,
        branch: str | None = None,
        depth: int = Query(default=2, ge=1, le=6),
        max_nodes: int = Query(default=60, ge=1, le=500),
    ) -> dict[str, Any]:
        if type not in _diagram_types and type not in _llm_diagram_types:
            raise HTTPException(
                400,
                f"type must be one of {sorted(_diagram_types | _llm_diagram_types)}; got {type!r}.",
            )
        storage = _storage()
        conn = storage.conn
        from ..diagrams import call_graph, class_diagram, module_dependency

        resolved_repo = repo_id or ""
        resolved_branch = branch or ""
        # Default to the busiest (repo, branch) in the store when not given, so
        # the single-repo web UI works without the caller passing repo_id.
        if not resolved_repo:
            row = conn.execute(
                "SELECT repo_id, branch_name FROM memories "
                "GROUP BY repo_id, branch_name ORDER BY COUNT(*) DESC LIMIT 1"
            ).fetchone()
            if row:
                resolved_repo = row[0]
                resolved_branch = resolved_branch or row[1]

        # LLM-synthesized types: delegate to the shared generate_diagram impl,
        # which grounds the prompt and either renders via a configured provider
        # or returns a passthrough_prompt for the user to paste into a chat model.
        # The route is sync (threadpool), so asyncio.run is safe here.
        if type in _llm_diagram_types:
            import asyncio

            from ..core.git_manager import GitManager
            from ..core.llm_adapter import LLMAdapter
            from ..tools.generate_diagram import GenerateDiagramParams, generate_diagram

            class _MM:
                def __init__(self, s):
                    self.storage_manager = s
                    self.git_manager = GitManager()

            resp = asyncio.run(
                generate_diagram(
                    GenerateDiagramParams(
                        type=type,
                        scope=scope,
                        repo_id=resolved_repo or None,
                        branch=resolved_branch or None,
                        depth=depth,
                        max_nodes=max_nodes,
                    ),
                    _MM(storage),
                    LLMAdapter(),
                )
            )
            from ..diagrams import is_empty_diagram

            return {
                "type": type,
                "mermaid": resp.mermaid,
                "truncated": resp.truncated,
                "passthrough": resp.passthrough,
                "passthrough_prompt": resp.passthrough_prompt,
                "success": resp.success,
                "message": resp.message,
                "empty": bool(resp.mermaid) and is_empty_diagram(resp.mermaid),
            }

        if type == "class":
            mermaid = class_diagram(conn, resolved_repo, resolved_branch, scope=scope)
            truncated = False
        elif type == "module":
            mermaid = module_dependency(conn, resolved_repo, resolved_branch, max_nodes=max_nodes)
            truncated = "%% truncated" in mermaid
        else:  # call
            if not scope:
                raise HTTPException(
                    400, "scope (memory_id or function_name) required for call graph"
                )
            import re

            _uuid = re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-" r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                scope,
            )
            if _uuid:
                root_id = scope
            else:
                row = conn.execute(
                    "SELECT id FROM memories WHERE repo_id = ? AND branch_name = ? "
                    "AND function_name = ? AND stale = 0 LIMIT 1",
                    (resolved_repo, resolved_branch, scope),
                ).fetchone()
                if row is None:
                    # Last resort: scope as a literal memory id.
                    row = conn.execute("SELECT id FROM memories WHERE id = ?", (scope,)).fetchone()
                if row is None:
                    raise HTTPException(
                        404,
                        f"function {scope!r} not found in repo={resolved_repo!r} branch={resolved_branch!r}",
                    )
                root_id = row["id"]
            mermaid = call_graph(conn, root_id, depth=depth, max_nodes=max_nodes)
            truncated = "%% truncated" in mermaid

        from ..diagrams import is_empty_diagram

        return {
            "type": type,
            "mermaid": mermaid,
            "truncated": truncated,
            "passthrough": False,
            "passthrough_prompt": "",
            "success": True,
            "message": "",
            "empty": is_empty_diagram(mermaid),
        }

    @app.get("/scopes")
    def list_scopes(
        repo_id: str | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        """Return distinct file_path and class_name values to populate UI dropdowns."""
        storage = _storage()
        conditions: list[str] = []
        params: list = []
        if repo_id:
            conditions.append("repo_id = ?")
            params.append(repo_id)
        if branch:
            conditions.append("branch_name = ?")
            params.append(branch)
        file_conditions = list(conditions) + ["file_path IS NOT NULL"]
        class_conditions = list(conditions) + ["class_name IS NOT NULL"]
        file_where = " WHERE " + " AND ".join(file_conditions)
        class_where = " WHERE " + " AND ".join(class_conditions)
        file_rows = storage.conn.execute(
            f"SELECT DISTINCT file_path FROM memories{file_where} ORDER BY file_path LIMIT 200",
            params,
        ).fetchall()
        class_rows = storage.conn.execute(
            f"SELECT DISTINCT class_name FROM memories{class_where} ORDER BY class_name LIMIT 200",
            params,
        ).fetchall()
        return {
            "files": [r["file_path"] for r in file_rows],
            "classes": [r["class_name"] for r in class_rows],
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
        import asyncio as _asyncio

        from ..server import initialize_mememo

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
