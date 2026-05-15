"""
Storage Manager for mememo.

Handles all data persistence using hybrid storage:
- SQLite for metadata (searchable, indexed)
- JSON blobs for content (deduplicated by checksum)
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ..types import (
    BACKFILL_SHA,
    BranchContext,
    BranchState,
    GitContext,
    Memory,
    MemoryContent,
    MemoryEvent,
    MemoryEventOp,
    MemoryFilters,
    MemoryMetadata,
    MemoryRelationships,
    MemorySummary,
    Relation,
    RelationType,
    RepoContext,
)


class StorageManager:
    """
    StorageManager handles all data persistence.

    Uses hybrid storage:
    - SQLite for metadata (fast queries, indexes)
    - JSON blobs for content (deduplicated by checksum)
    """

    def __init__(self, base_dir: Path, encryption_key: str | None = None):
        """
        Initialize storage manager.

        Args:
            base_dir: Base directory for all data
            encryption_key: Optional encryption key for SQLite encryption
        """
        self.base_dir = Path(base_dir)
        self.content_dir = self.base_dir / "content"

        # Ensure directories exist
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.content_dir.mkdir(parents=True, exist_ok=True)

        # Initialize SQLite database
        db_path = self.base_dir / "mememo.db"
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        # Enable WAL mode for better concurrency
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

        # Optional encryption (requires sqlcipher)
        if encryption_key:
            self.conn.execute(f"PRAGMA key='{encryption_key}'")
            self.conn.execute("PRAGMA cipher_page_size=4096")
            self.conn.execute("PRAGMA kdf_iter=256000")

        # Initialize schema
        self._initialize_schema()
        self._migrate_schema()

    def _initialize_schema(self) -> None:
        """Initialize database schema with all tables and indexes."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                repo_id TEXT NOT NULL,
                repo_name TEXT,
                repo_path TEXT,
                branch_name TEXT NOT NULL,
                commit_hash TEXT,
                content_type TEXT NOT NULL,
                file_path TEXT,
                line_start INTEGER,
                line_end INTEGER,

                -- NEW in v0.3.0: Code-aware metadata
                function_name TEXT,
                class_name TEXT,
                language TEXT,
                chunk_type TEXT,

                checksum TEXT NOT NULL,
                content_ref TEXT NOT NULL,
                token_count INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                embedding_shard INTEGER,
                embedding_index INTEGER,
                stale INTEGER DEFAULT 0,
                stale_reason TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_repo_branch ON memories(repo_id, branch_name);
            CREATE INDEX IF NOT EXISTS idx_file_path ON memories(file_path);
            CREATE INDEX IF NOT EXISTS idx_checksum ON memories(checksum);
            CREATE INDEX IF NOT EXISTS idx_created_at ON memories(created_at);
            CREATE INDEX IF NOT EXISTS idx_content_type ON memories(content_type);

            -- NEW in v0.3.0: Code-aware indexes
            CREATE INDEX IF NOT EXISTS idx_function_name ON memories(function_name);
            CREATE INDEX IF NOT EXISTS idx_class_name ON memories(class_name);
            CREATE INDEX IF NOT EXISTS idx_language ON memories(language);
            CREATE INDEX IF NOT EXISTS idx_chunk_type ON memories(chunk_type);

            CREATE TABLE IF NOT EXISTS tags (
                memory_id TEXT NOT NULL,
                tag TEXT NOT NULL,
                PRIMARY KEY (memory_id, tag),
                FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_tag ON tags(tag);

            CREATE TABLE IF NOT EXISTS relationships (
                from_memory_id TEXT NOT NULL,
                to_memory_id TEXT NOT NULL,
                relationship_type TEXT NOT NULL,
                PRIMARY KEY (from_memory_id, to_memory_id, relationship_type),
                FOREIGN KEY (from_memory_id) REFERENCES memories(id) ON DELETE CASCADE,
                FOREIGN KEY (to_memory_id) REFERENCES memories(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_from_memory ON relationships(from_memory_id);
            CREATE INDEX IF NOT EXISTS idx_to_memory ON relationships(to_memory_id);

            CREATE TABLE IF NOT EXISTS index_state (
                repo_id TEXT NOT NULL,
                branch_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_checksum TEXT NOT NULL,
                indexed_at INTEGER NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                PRIMARY KEY (repo_id, branch_name, file_path)
            );

            CREATE INDEX IF NOT EXISTS idx_index_status ON index_state(status);
            CREATE INDEX IF NOT EXISTS idx_index_checksum ON index_state(file_checksum);

            CREATE TABLE IF NOT EXISTS repo_index_metadata (
                repo_id TEXT NOT NULL,
                branch_name TEXT NOT NULL,
                last_full_index INTEGER,
                last_incremental_index INTEGER,
                total_files INTEGER DEFAULT 0,
                indexed_files INTEGER DEFAULT 0,
                failed_files INTEGER DEFAULT 0,
                last_indexed_commit TEXT,
                PRIMARY KEY (repo_id, branch_name)
            );
        """)
        self.conn.commit()

    def _migrate_schema(self) -> None:
        """Apply incremental schema migrations (idempotent — safe to run on every startup)."""
        migrations = [
            "ALTER TABLE memories ADD COLUMN stale INTEGER DEFAULT 0",
            "ALTER TABLE memories ADD COLUMN stale_reason TEXT",
            "ALTER TABLE repo_index_metadata ADD COLUMN last_indexed_commit TEXT",
            # v0.4.0 commit-aware foundation (FR-001, FR-002, FR-007)
            "ALTER TABLE memories ADD COLUMN created_at_sha TEXT",
            "ALTER TABLE memories ADD COLUMN updated_at_sha TEXT",
            "ALTER TABLE memories ADD COLUMN risk_grade TEXT",
        ]
        for sql in migrations:
            try:
                self.conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # Column already exists
        self.conn.commit()

        # v0.4.0 new tables — memory_events (FR-003) and branch_state (FR-011).
        # commit_sha is NOT NULL and length-checked at the DB layer to defend
        # against empty-string sentinels leaking into the event log (security
        # finding from magent-security_engineer audit, 2026-05-12).
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                commit_sha TEXT NOT NULL CHECK (length(commit_sha) = 40),
                memory_id TEXT NOT NULL,
                op TEXT NOT NULL CHECK (op IN ('CREATED','UPDATED','STALED','DELETED','RESTORED')),
                content_sha TEXT,
                branch TEXT NOT NULL,
                ts INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_commit ON memory_events(commit_sha);
            CREATE INDEX IF NOT EXISTS idx_events_memory ON memory_events(memory_id, ts);
            CREATE INDEX IF NOT EXISTS idx_events_branch ON memory_events(branch, ts);
            -- Defends against duplicate synthetic events under concurrent startup;
            -- a memory may only be CREATED once per (memory_id, commit_sha).
            CREATE UNIQUE INDEX IF NOT EXISTS idx_events_unique_create
                ON memory_events(memory_id, commit_sha) WHERE op = 'CREATED';

            CREATE TABLE IF NOT EXISTS branch_state (
                repo_id TEXT NOT NULL,
                branch TEXT NOT NULL,
                last_indexed_sha TEXT,
                parent_sha TEXT,
                PRIMARY KEY (repo_id, branch)
            );

            -- v0.5 (FR-017): typed edge layer with confidence + commit SHA.
            CREATE TABLE IF NOT EXISTS relations (
                id TEXT PRIMARY KEY,
                repo_id TEXT NOT NULL,
                branch TEXT NOT NULL,
                source_memory_id TEXT NOT NULL,
                target_memory_id TEXT,
                target_symbol TEXT,
                type TEXT NOT NULL CHECK (type IN ('IMPORTS','CALLS','EXTENDS','IMPLEMENTS','USES','DECORATED_BY')),
                confidence TEXT NOT NULL CHECK (confidence IN ('EXTRACTED','INFERRED','AMBIGUOUS')),
                created_at_sha TEXT NOT NULL CHECK (length(created_at_sha) = 40),
                stale INTEGER DEFAULT 0,
                community INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_rel_src ON relations(source_memory_id, type);
            CREATE INDEX IF NOT EXISTS idx_rel_tgt ON relations(target_memory_id, type);
            CREATE INDEX IF NOT EXISTS idx_rel_repo ON relations(repo_id, branch);

            -- v0.5 (FR-019): entity dedup alias map; populated by graph_analysis.
            CREATE TABLE IF NOT EXISTS entity_aliases (
                canonical_memory_id TEXT NOT NULL,
                alias_label TEXT NOT NULL,
                similarity REAL NOT NULL,
                PRIMARY KEY (canonical_memory_id, alias_label)
            );
        """)
        self.conn.commit()

        # v0.3 -> v0.4 idempotent backfill (FR-001, FR-002, FR-003 / Task T011).
        # If memories exist without created_at_sha, seed from branch.commit_hash
        # (the existing per-memory commit) and emit a synthetic CREATED event so
        # event-replay reconstruction works on data minted before this release.
        self._backfill_v04_commit_metadata()

    def _backfill_v04_commit_metadata(self) -> None:
        """One-shot, idempotent backfill of created_at_sha + seed memory_events.

        Runs inside a single transaction. Safe to call repeatedly: each statement
        is a no-op once data has been seeded.

        Pre-v0.4 rows that lack a real commit_hash receive the BACKFILL_SHA
        sentinel (distinct from NULL_SHA used by live non-repo writes) so the
        replay path can tell them apart. The CHECK(length=40) constraint plus
        the unique CREATED index defend against bad sentinels and concurrent
        double-seeds (magent-security_engineer audit, 2026-05-12).
        """
        cursor = self.conn.cursor()
        # Backfill created_at_sha / updated_at_sha from the existing commit_hash
        # column on memories that pre-date v0.4. Only rows that actually had a
        # valid-length commit_hash get it; everything else stays NULL on the
        # memories row (the synthetic event below carries BACKFILL_SHA instead).
        cursor.execute(
            "UPDATE memories SET created_at_sha = commit_hash "
            "WHERE created_at_sha IS NULL AND length(commit_hash) = 40"
        )
        cursor.execute(
            "UPDATE memories SET updated_at_sha = commit_hash "
            "WHERE updated_at_sha IS NULL AND length(commit_hash) = 40"
        )
        # Seed a synthetic CREATED event per pre-existing memory that has no event yet.
        # Use the row's commit_hash when it's a real SHA, else BACKFILL_SHA.
        # INSERT OR IGNORE makes the operation a no-op on the second startup
        # thanks to idx_events_unique_create.
        cursor.execute(
            f"""
            INSERT OR IGNORE INTO memory_events (commit_sha, memory_id, op, content_sha, branch, ts)
            SELECT
                CASE
                    WHEN length(m.commit_hash) = 40 THEN m.commit_hash
                    ELSE '{BACKFILL_SHA}'
                END AS commit_sha,
                m.id,
                'CREATED',
                m.checksum,
                m.branch_name,
                m.created_at
            FROM memories m
            WHERE NOT EXISTS (
                SELECT 1 FROM memory_events e WHERE e.memory_id = m.id
            )
            """
        )
        self.conn.commit()

    def _get_content_path(self, checksum: str) -> Path:
        """
        Get content blob path from checksum.
        Uses first 2 chars of checksum for directory sharding.

        Args:
            checksum: SHA-256 checksum

        Returns:
            Path to content JSON file
        """
        prefix = checksum[:2]
        return self.content_dir / prefix / f"{checksum}.json"

    async def save_memory(self, memory: Memory) -> None:
        """
        Save memory to storage.

        Args:
            memory: Memory object to save
        """
        content_path = self._get_content_path(memory.metadata.checksum)

        # Ensure content directory exists
        content_path.parent.mkdir(parents=True, exist_ok=True)

        # Track whether blob existed before this call (content-addressable dedup)
        blob_existed = content_path.exists()

        # Save content blob (deduplicated by checksum)
        content_blob = {
            "text": memory.content.text,
            "language": memory.content.language,
            "summary": memory.summary.model_dump(),
            # NEW in v0.3.0: Code-aware metadata
            "function_name": memory.content.function_name,
            "class_name": memory.content.class_name,
            "docstring": memory.content.docstring,
            "decorators": memory.content.decorators,
            "parent_class": memory.content.parent_class,
        }
        content_path.write_text(json.dumps(content_blob, indent=2), encoding="utf-8")

        # Get relative path for storage
        content_ref = str(content_path.relative_to(self.base_dir))

        # Insert into SQLite — roll back and clean up JSON blob on failure
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO memories (
                    id, repo_id, repo_name, repo_path, branch_name, commit_hash,
                    content_type, file_path, line_start, line_end,
                    function_name, class_name, language, chunk_type,
                    checksum, content_ref, token_count, created_at, updated_at,
                    embedding_shard, embedding_index, stale, stale_reason,
                    created_at_sha, updated_at_sha, risk_grade
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    memory.id,
                    memory.repo.id,
                    memory.repo.name,
                    memory.repo.path,
                    memory.branch.name,
                    memory.branch.commit_hash,
                    memory.content.type,
                    memory.content.file_path,
                    memory.content.line_range[0] if memory.content.line_range else None,
                    memory.content.line_range[1] if memory.content.line_range else None,
                    memory.content.function_name,
                    memory.content.class_name,
                    memory.content.language,
                    self._infer_chunk_type(memory.content),
                    memory.metadata.checksum,
                    content_ref,
                    memory.metadata.token_count,
                    int(memory.metadata.created_at.timestamp()),
                    int(memory.metadata.updated_at.timestamp()),
                    memory.metadata.embedding_shard,
                    memory.metadata.embedding_index,
                    1 if memory.metadata.stale else 0,
                    memory.metadata.stale_reason,
                    # v0.4.0 commit-aware columns
                    memory.metadata.created_at_sha,
                    memory.metadata.updated_at_sha,
                    memory.metadata.risk_grade,
                ),
            )

            # Insert tags
            if memory.metadata.tags:
                cursor.executemany(
                    "INSERT INTO tags (memory_id, tag) VALUES (?, ?)",
                    [(memory.id, tag) for tag in memory.metadata.tags],
                )

            # Insert relationships
            if memory.relationships.depends_on:
                cursor.executemany(
                    "INSERT INTO relationships (from_memory_id, to_memory_id, relationship_type) VALUES (?, ?, ?)",
                    [
                        (memory.id, dep_id, "depends_on")
                        for dep_id in memory.relationships.depends_on
                    ],
                )

            if memory.relationships.related_to:
                cursor.executemany(
                    "INSERT INTO relationships (from_memory_id, to_memory_id, relationship_type) VALUES (?, ?, ?)",
                    [
                        (memory.id, rel_id, "related_to")
                        for rel_id in memory.relationships.related_to
                    ],
                )

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            if not blob_existed:
                try:
                    content_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    def _infer_chunk_type(self, content: MemoryContent) -> str:
        """Infer chunk type from content metadata."""
        if content.function_name:
            return "function"
        elif content.class_name:
            return "class"
        else:
            return "text"

    async def load_memory(self, id: str, context: GitContext) -> Memory:
        """
        Load memory by ID.

        Args:
            id: Memory ID
            context: Git context for filtering

        Returns:
            Memory object

        Raises:
            ValueError: If memory not found
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM memories
            WHERE id = ? AND repo_id = ? AND branch_name = ?
        """,
            (id, context.repo.id, context.branch.name),
        )

        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Memory not found: {id}")

        return await self._row_to_memory(dict(row))

    async def _row_to_memory(self, row: dict) -> Memory:
        """
        Convert database row to Memory object.

        Args:
            row: SQLite row as dict

        Returns:
            Memory object
        """
        # Load content blob
        content_path = self.base_dir / row["content_ref"]
        content_blob = json.loads(content_path.read_text(encoding="utf-8"))

        # Load tags
        cursor = self.conn.cursor()
        cursor.execute("SELECT tag FROM tags WHERE memory_id = ?", (row["id"],))
        tags = [r["tag"] for r in cursor.fetchall()]

        # Load relationships
        cursor.execute(
            "SELECT to_memory_id FROM relationships WHERE from_memory_id = ? AND relationship_type = ?",
            (row["id"], "depends_on"),
        )
        depends_on = [r["to_memory_id"] for r in cursor.fetchall()]

        cursor.execute(
            "SELECT to_memory_id FROM relationships WHERE from_memory_id = ? AND relationship_type = ?",
            (row["id"], "related_to"),
        )
        related_to = [r["to_memory_id"] for r in cursor.fetchall()]

        return Memory(
            id=row["id"],
            repo=RepoContext(
                id=row["repo_id"],
                name=row["repo_name"],
                path=row["repo_path"],
                remote_url=None,  # Not stored in DB
            ),
            branch=BranchContext(
                name=row["branch_name"],
                commit_hash=row["commit_hash"],
            ),
            content=MemoryContent(
                type=row["content_type"],
                text=content_blob["text"],
                language=content_blob.get("language"),
                file_path=row["file_path"],
                line_range=(
                    (row["line_start"], row["line_end"]) if row["line_start"] is not None else None
                ),
                # NEW in v0.3.0
                function_name=content_blob.get("function_name"),
                class_name=content_blob.get("class_name"),
                docstring=content_blob.get("docstring"),
                decorators=content_blob.get("decorators"),
                parent_class=content_blob.get("parent_class"),
            ),
            metadata=MemoryMetadata(
                tags=tags,
                created_at=datetime.fromtimestamp(row["created_at"]),
                updated_at=datetime.fromtimestamp(row["updated_at"]),
                checksum=row["checksum"],
                token_count=row["token_count"],
                embedding_shard=row["embedding_shard"],
                embedding_index=row["embedding_index"],
                stale=bool(row.get("stale", 0)),
                stale_reason=row.get("stale_reason"),
                # v0.4.0 commit-aware metadata (may be None on rows pre-backfill)
                created_at_sha=row.get("created_at_sha"),
                updated_at_sha=row.get("updated_at_sha"),
                risk_grade=row.get("risk_grade"),
            ),
            relationships=MemoryRelationships(
                depends_on=depends_on if depends_on else None,
                related_to=related_to if related_to else None,
            ),
            summary=MemorySummary(**content_blob["summary"]),
        )

    async def find_memories(self, filters: MemoryFilters, context: GitContext) -> list[Memory]:
        """
        Find memories with filters.

        Args:
            filters: Query filters
            context: Git context

        Returns:
            List of matching memories
        """
        conditions = []
        params = []

        # Repo/branch filter
        if not filters.cross_branch:
            conditions.append("m.repo_id = ?")
            params.append(context.repo.id)
            conditions.append("m.branch_name = ?")
            params.append(context.branch.name)
        elif filters.repo_id:
            conditions.append("m.repo_id = ?")
            params.append(filters.repo_id)

        # Other filters
        if filters.id:
            conditions.append("m.id = ?")
            params.append(filters.id)

        if filters.file_path:
            conditions.append("m.file_path LIKE ?")
            params.append(f"{filters.file_path}%")

        if filters.type:
            conditions.append("m.content_type = ?")
            params.append(filters.type)

        if filters.language:
            conditions.append("m.language = ?")
            params.append(filters.language)

        if filters.function_name:
            conditions.append("m.function_name = ?")
            params.append(filters.function_name)

        if filters.class_name:
            conditions.append("m.class_name = ?")
            params.append(filters.class_name)

        if not filters.include_stale:
            conditions.append("m.stale = 0")

        # Tag filter (requires join, AND logic: all tags must match)
        query = "SELECT DISTINCT m.* FROM memories m"
        tag_count = 0
        if filters.tags:
            tag_count = len(filters.tags)
            query += " INNER JOIN tags t ON m.id = t.memory_id"
            placeholders = ",".join("?" * tag_count)
            conditions.append(f"t.tag IN ({placeholders})")
            params.extend(filters.tags)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        # AND logic: require all tags present (not just any)
        if tag_count > 1:
            query += f" GROUP BY m.id HAVING COUNT(DISTINCT t.tag) = {tag_count}"

        # Sorting
        if filters.sort_by == "date":
            query += " ORDER BY m.created_at DESC"
        elif filters.sort_by == "file":
            query += " ORDER BY m.file_path ASC, m.created_at DESC"
        elif filters.sort_by == "type":
            query += " ORDER BY m.content_type ASC, m.created_at DESC"

        # Pagination
        query += " LIMIT ? OFFSET ?"
        params.extend([filters.limit, filters.offset])

        cursor = self.conn.cursor()
        cursor.execute(query, params)

        memories = []
        for row in cursor.fetchall():
            memory = await self._row_to_memory(dict(row))
            memories.append(memory)

        return memories

    async def delete_memory(self, id: str, context: GitContext) -> None:
        """
        Delete memory.

        Args:
            id: Memory ID
            context: Git context
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "DELETE FROM memories WHERE id = ? AND repo_id = ? AND branch_name = ?",
            (id, context.repo.id, context.branch.name),
        )
        self.conn.commit()

    def delete_expired_memories(
        self, ttl_conversation_days: int, ttl_context_days: int
    ) -> list[str]:
        """
        Delete expired memories based on per-type TTL.

        Only conversation and context types are subject to TTL.
        decision, analysis, and summary are durable and never auto-expired.
        Returns IDs of deleted memories so the caller can clean up the vector index.
        A TTL of 0 means no expiry for that type.
        """
        import time

        now = int(time.time())
        deleted_ids: list[str] = []
        cursor = self.conn.cursor()

        for content_type, ttl_days in (
            ("conversation", ttl_conversation_days),
            ("context", ttl_context_days),
        ):
            if ttl_days <= 0:
                continue
            cutoff = now - (ttl_days * 86400)
            cursor.execute(
                "SELECT id FROM memories WHERE content_type=? AND created_at<?",
                (content_type, cutoff),
            )
            ids = [row[0] for row in cursor.fetchall()]
            if ids:
                placeholders = ",".join("?" * len(ids))
                cursor.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", ids)
                deleted_ids.extend(ids)

        if deleted_ids:
            self.conn.commit()

        return deleted_ids

    def get_statistics(self) -> dict:
        """
        Get storage statistics.

        Returns:
            Dict with total memories and breakdown by repo
        """
        cursor = self.conn.cursor()

        # Total memories
        cursor.execute("SELECT COUNT(*) as count FROM memories")
        total = cursor.fetchone()["count"]

        # By repo
        cursor.execute("""
            SELECT repo_id, repo_name, COUNT(*) as count
            FROM memories
            GROUP BY repo_id, repo_name
        """)
        by_repo = {f"{r['repo_name']} ({r['repo_id']})": r["count"] for r in cursor.fetchall()}

        # Calculate total size (estimate)
        # Rough estimate: 3KB per memory (embeddings + metadata + content)
        total_size_mb = (total * 3) / 1024

        return {
            "total_memories": total,
            "by_repo": by_repo,
            "total_size_mb": total_size_mb,
        }

    def mark_memories_stale_for_file(
        self,
        file_path: str,
        repo_id: str,
        branch: str,
        reason: str,
        commit_sha: str | None = None,
    ) -> int:
        """
        Mark code memories for a file as stale.

        Only affects CODE_MEMORY_TYPES (code_snippet, relationship).
        Persistent types (decision, analysis, conversation, context, summary) are never staled.

        v0.4 addition (FR-003): emit a STALED event per affected memory so the
        event log can replay the staleness transition during time-travel. Pass
        commit_sha to identify the commit that caused the staling; defaults to
        NULL_SHA when the caller has no git context.

        Returns:
            Number of memories marked stale
        """
        from ..types.memory import CODE_MEMORY_TYPES, coerce_sha

        placeholders = ",".join("?" * len(CODE_MEMORY_TYPES))
        cursor = self.conn.cursor()
        # First collect the ids we are about to stale — needed for event emission.
        cursor.execute(
            f"""
            SELECT id, checksum FROM memories
            WHERE file_path = ? AND repo_id = ? AND branch_name = ?
              AND content_type IN ({placeholders})
              AND stale = 0
            """,
            (file_path, repo_id, branch, *CODE_MEMORY_TYPES),
        )
        affected = [(row["id"], row["checksum"]) for row in cursor.fetchall()]
        if not affected:
            return 0

        cursor.execute(
            f"""
            UPDATE memories
            SET stale = 1, stale_reason = ?
            WHERE file_path = ? AND repo_id = ? AND branch_name = ?
              AND content_type IN ({placeholders})
              AND stale = 0
            """,
            (reason, file_path, repo_id, branch, *CODE_MEMORY_TYPES),
        )
        rowcount = cursor.rowcount

        # NULL_SHA fallback when the caller has no real git context.
        sha = coerce_sha(commit_sha)
        ts = int(datetime.now().timestamp())
        cursor.executemany(
            """
            INSERT INTO memory_events (commit_sha, memory_id, op, content_sha, branch, ts)
            VALUES (?, ?, 'STALED', ?, ?, ?)
            """,
            [(sha, mid, chk, branch, ts) for mid, chk in affected],
        )
        self.conn.commit()
        return rowcount

    def emit_update_event(
        self,
        memory_id: str,
        *,
        commit_sha: str,
        content_sha: str | None,
        branch: str,
    ) -> int:
        """Emit a single UPDATED event. Future update_memory paths call this
        after persisting the new state. Returns inserted rowid.
        """
        return self.append_event(
            MemoryEvent(
                commit_sha=commit_sha,
                memory_id=memory_id,
                op="UPDATED",
                content_sha=content_sha,
                branch=branch,
            )
        )

    # ----- T004: event-replay query (FR-004, FR-012) ----------------------

    def state_at_ts(
        self,
        memory_id: str,
        target_ts: int,
        branch: str | None = None,
    ) -> tuple[str, MemoryEventOp] | None:
        """Reconstruct a memory's state at target_ts by replaying events.

        Returns:
            (content_sha, last_op) if the memory was alive at target_ts —
              last_op is CREATED, UPDATED, STALED, or RESTORED.
            None if the memory had no event at or before target_ts (it didn't
              exist yet) OR if its most recent event was DELETED.

        Per clarifications.json: replay is purely timestamp-based; the caller
        is responsible for converting a target SHA into target_ts via git.
        """
        sql = (
            "SELECT op, content_sha FROM memory_events "
            "WHERE memory_id = ? AND ts <= ?"
        )
        params: list[object] = [memory_id, target_ts]
        if branch is not None:
            sql += " AND branch = ?"
            params.append(branch)
        sql += " ORDER BY ts DESC, id DESC LIMIT 1"
        row = self.conn.execute(sql, params).fetchone()
        if row is None:
            return None
        if row["op"] == "DELETED":
            return None
        return (row["content_sha"], row["op"])

    def alive_memory_ids_at_ts(
        self,
        target_ts: int,
        repo_id: str | None = None,
        branch: str | None = None,
    ) -> set[str]:
        """Bulk replay — return the set of memory_ids alive at target_ts.

        Used by recall_at_commit (T010) to filter FAISS search results to the
        subset that existed at the target SHA. Implemented with a window
        function (ROW_NUMBER OVER PARTITION BY memory_id ORDER BY ts DESC) so
        the query is one pass + one sort rather than an O(E^2) correlated
        subquery (performance audit 2026-05-13). Requires SQLite 3.25+.
        FR-005: no materialised snapshots — pure replay against the event log.
        """
        inner_where = ["ts <= ?"]
        inner_params: list[object] = [target_ts]
        if branch is not None:
            inner_where.append("branch = ?")
            inner_params.append(branch)
        inner_sql = (
            "SELECT memory_id, op, "
            "ROW_NUMBER() OVER (PARTITION BY memory_id ORDER BY ts DESC, id DESC) AS rn "
            f"FROM memory_events WHERE {' AND '.join(inner_where)}"
        )
        if repo_id is None:
            sql = f"SELECT memory_id FROM ({inner_sql}) WHERE rn = 1 AND op != 'DELETED'"
            rows = self.conn.execute(sql, inner_params).fetchall()
            return {r["memory_id"] for r in rows}

        # repo_id is on memories, not memory_events — join.
        sql = (
            f"SELECT e.memory_id FROM ({inner_sql}) AS e "
            "INNER JOIN memories m ON m.id = e.memory_id "
            "WHERE e.rn = 1 AND e.op != 'DELETED' AND m.repo_id = ?"
        )
        rows = self.conn.execute(sql, [*inner_params, repo_id]).fetchall()
        return {r["memory_id"] for r in rows}

    def get_last_indexed_commit(self, repo_id: str, branch: str) -> str | None:
        """Return the commit hash recorded during the last index_repository run, or None."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT last_indexed_commit FROM repo_index_metadata WHERE repo_id = ? AND branch_name = ?",
            (repo_id, branch),
        )
        row = cursor.fetchone()
        return row["last_indexed_commit"] if row else None

    def set_last_indexed_commit(self, repo_id: str, branch: str, commit_hash: str) -> None:
        """Record the commit hash for the last successful index_repository run."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO repo_index_metadata (repo_id, branch_name, last_indexed_commit)
            VALUES (?, ?, ?)
            ON CONFLICT(repo_id, branch_name)
            DO UPDATE SET last_indexed_commit = excluded.last_indexed_commit
            """,
            (repo_id, branch, commit_hash),
        )
        self.conn.commit()

    # ----- v0.4.0 commit-aware additions ----------------------------------

    def append_event(self, event: MemoryEvent) -> int:
        """Append a memory_event row (FR-003). Returns the inserted rowid."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO memory_events (commit_sha, memory_id, op, content_sha, branch, ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.commit_sha,
                event.memory_id,
                event.op,
                event.content_sha,
                event.branch,
                int(event.ts.timestamp()),
            ),
        )
        self.conn.commit()
        return cursor.lastrowid or 0

    def list_events(
        self,
        memory_id: str | None = None,
        branch: str | None = None,
        op: MemoryEventOp | None = None,
    ) -> list[MemoryEvent]:
        """Read memory_events filtered by any subset of (memory_id, branch, op)."""
        conditions: list[str] = []
        params: list[object] = []
        if memory_id is not None:
            conditions.append("memory_id = ?")
            params.append(memory_id)
        if branch is not None:
            conditions.append("branch = ?")
            params.append(branch)
        if op is not None:
            conditions.append("op = ?")
            params.append(op)
        sql = "SELECT * FROM memory_events"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY ts ASC, id ASC"
        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        out: list[MemoryEvent] = []
        for row in cursor.fetchall():
            out.append(
                MemoryEvent(
                    id=row["id"],
                    commit_sha=row["commit_sha"],
                    memory_id=row["memory_id"],
                    op=row["op"],
                    content_sha=row["content_sha"],
                    branch=row["branch"],
                    ts=datetime.fromtimestamp(row["ts"]),
                )
            )
        return out

    def get_branch_state(self, repo_id: str, branch: str) -> BranchState | None:
        """Read the persisted branch_state row, or None."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM branch_state WHERE repo_id = ? AND branch = ?",
            (repo_id, branch),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return BranchState(
            repo_id=row["repo_id"],
            branch=row["branch"],
            last_indexed_sha=row["last_indexed_sha"],
            parent_sha=row["parent_sha"],
        )

    def upsert_branch_state(self, state: BranchState) -> None:
        """Upsert branch_state (FR-011)."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO branch_state (repo_id, branch, last_indexed_sha, parent_sha)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(repo_id, branch) DO UPDATE SET
                last_indexed_sha = excluded.last_indexed_sha,
                parent_sha = excluded.parent_sha
            """,
            (state.repo_id, state.branch, state.last_indexed_sha, state.parent_sha),
        )
        self.conn.commit()

    def update_memory_shas(
        self, memory_id: str, *, created_at_sha: str | None = None, updated_at_sha: str | None = None
    ) -> None:
        """Surgical update of the v0.4 SHA columns; leaves nulls intact when not provided."""
        sets: list[str] = []
        params: list[object] = []
        if created_at_sha is not None:
            sets.append("created_at_sha = ?")
            params.append(created_at_sha)
        if updated_at_sha is not None:
            sets.append("updated_at_sha = ?")
            params.append(updated_at_sha)
        if not sets:
            return
        params.append(memory_id)
        self.conn.execute(
            f"UPDATE memories SET {', '.join(sets)} WHERE id = ?", params
        )
        self.conn.commit()

    # ----- v0.5.0 typed-edge layer (FR-017, FR-020, FR-021, FR-022) -------

    def insert_relations(self, relations: list[Relation]) -> int:
        """Bulk-insert edges. Returns rowcount."""
        if not relations:
            return 0
        cursor = self.conn.cursor()
        cursor.executemany(
            """
            INSERT INTO relations (
                id, repo_id, branch, source_memory_id, target_memory_id,
                target_symbol, type, confidence, created_at_sha, stale, community
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r.id, r.repo_id, r.branch, r.source_memory_id, r.target_memory_id,
                    r.target_symbol, r.type, r.confidence, r.created_at_sha,
                    1 if r.stale else 0, r.community,
                )
                for r in relations
            ],
        )
        self.conn.commit()
        return cursor.rowcount

    def list_relations(
        self,
        *,
        repo_id: str | None = None,
        branch: str | None = None,
        source_memory_id: str | None = None,
        target_memory_id: str | None = None,
        type: RelationType | None = None,
    ) -> list[Relation]:
        conditions: list[str] = []
        params: list[object] = []
        for col, val in [
            ("repo_id", repo_id),
            ("branch", branch),
            ("source_memory_id", source_memory_id),
            ("target_memory_id", target_memory_id),
            ("type", type),
        ]:
            if val is not None:
                conditions.append(f"{col} = ?")
                params.append(val)
        sql = "SELECT * FROM relations"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        rows = self.conn.execute(sql, params).fetchall()
        return [
            Relation(
                id=row["id"],
                repo_id=row["repo_id"],
                branch=row["branch"],
                source_memory_id=row["source_memory_id"],
                target_memory_id=row["target_memory_id"],
                target_symbol=row["target_symbol"],
                type=row["type"],
                confidence=row["confidence"],
                created_at_sha=row["created_at_sha"],
                stale=bool(row["stale"]),
                community=row["community"],
            )
            for row in rows
        ]

    # ----- v0.6 worktree migration (T030 / FR-025) ------------------------

    def reassign_repo_id(self, from_repo_id: str, to_repo_id: str) -> dict[str, int]:
        """Rewrite repo_id across every table that carries it.

        Called by ``mememo migrate-worktrees`` when an old (per-worktree)
        repo_id needs to be folded into the canonical one. Idempotent: a
        no-op when ``from_repo_id`` has no rows.

        Returns row counts updated per table for audit logging.
        """
        if from_repo_id == to_repo_id:
            return {}
        cursor = self.conn.cursor()
        counts: dict[str, int] = {}

        tables = [
            ("memories", "repo_id"),
            ("relations", "repo_id"),
            ("branch_state", "repo_id"),
            ("repo_index_metadata", "repo_id"),
            ("index_state", "repo_id"),
        ]
        for table, col in tables:
            try:
                cursor.execute(
                    f"UPDATE {table} SET {col} = ? WHERE {col} = ?",
                    (to_repo_id, from_repo_id),
                )
                counts[table] = cursor.rowcount
            except sqlite3.OperationalError:
                # Table may not exist on older schemas; skip silently.
                continue
        self.conn.commit()
        return counts

    # ----- v0.4.0 down-migration (rollback) -------------------------------

    def downgrade_v04_to_v03(self, *, i_understand_this_is_destructive: bool = False) -> dict[str, int]:
        """Reverse the v0.4 schema additions for emergency rollback.

        Drops memory_events, branch_state, and the three new memories columns
        (created_at_sha, updated_at_sha, risk_grade). Requires SQLite 3.35+
        for ALTER TABLE DROP COLUMN.

        The caller MUST pass i_understand_this_is_destructive=True. The flag
        is deliberately ugly so accidental invocation from a CLI or test helper
        is impossible (security audit 2026-05-13).

        Returns a dict with row counts of the rows that were destroyed —
        callers should persist this to a log so the data loss is auditable.

        WARNING: this is a destructive, one-way operation. The synthetic
        CREATED events seeded by the v0.3 -> v0.4 backfill are part of
        what gets dropped; re-running the upgrade will recreate them
        idempotently, but any UPDATED / STALED / DELETED / RESTORED events
        emitted between the upgrade and the downgrade are permanently lost.
        """
        if not i_understand_this_is_destructive:
            raise RuntimeError(
                "downgrade_v04_to_v03 refuses to run without "
                "i_understand_this_is_destructive=True (destructive operation)."
            )
        import sqlite3 as _sqlite

        if _sqlite.sqlite_version_info < (3, 35, 0):
            raise RuntimeError(
                f"downgrade_v04_to_v03 requires SQLite 3.35+ for ALTER TABLE DROP COLUMN; "
                f"this build is {_sqlite.sqlite_version}"
            )
        cursor = self.conn.cursor()
        counts: dict[str, int] = {}
        for table in ("memory_events", "branch_state"):
            row = cursor.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            counts[table] = row["n"] if row else 0
        cursor.executescript(
            """
            DROP INDEX IF EXISTS idx_events_unique_create;
            DROP INDEX IF EXISTS idx_events_branch;
            DROP INDEX IF EXISTS idx_events_memory;
            DROP INDEX IF EXISTS idx_events_commit;
            DROP TABLE IF EXISTS memory_events;
            DROP TABLE IF EXISTS branch_state;
            ALTER TABLE memories DROP COLUMN risk_grade;
            ALTER TABLE memories DROP COLUMN updated_at_sha;
            ALTER TABLE memories DROP COLUMN created_at_sha;
            """
        )
        self.conn.commit()
        return counts

    # ----------------------------------------------------------------------

    def close(self) -> None:
        """Close database connection."""
        self.conn.close()

    def get_connection(self) -> sqlite3.Connection:
        """Get database connection for advanced queries."""
        return self.conn
