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
from typing import Optional

from ..types import (
    Memory,
    MemoryContent,
    MemoryMetadata,
    MemoryRelationships,
    MemorySummary,
    GitContext,
    RepoContext,
    BranchContext,
    MemoryFilters,
    MemoryContentType,
)


class StorageManager:
    """
    StorageManager handles all data persistence.

    Uses hybrid storage:
    - SQLite for metadata (fast queries, indexes)
    - JSON blobs for content (deduplicated by checksum)
    """

    def __init__(self, base_dir: Path, encryption_key: Optional[str] = None):
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
                embedding_index INTEGER
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
                PRIMARY KEY (repo_id, branch_name)
            );
        """)
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

        # Save content blob (deduplicated by checksum)
        content_blob = {
            "text": memory.content.text,
            "language": memory.content.language,
            "summary": memory.summary.dict(),
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

        # Insert into SQLite
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO memories (
                id, repo_id, repo_name, repo_path, branch_name, commit_hash,
                content_type, file_path, line_start, line_end,
                function_name, class_name, language, chunk_type,
                checksum, content_ref, token_count, created_at, updated_at,
                embedding_shard, embedding_index
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
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
            # NEW in v0.3.0
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
        ))

        # Insert tags
        if memory.metadata.tags:
            cursor.executemany(
                "INSERT INTO tags (memory_id, tag) VALUES (?, ?)",
                [(memory.id, tag) for tag in memory.metadata.tags]
            )

        # Insert relationships
        if memory.relationships.depends_on:
            cursor.executemany(
                "INSERT INTO relationships (from_memory_id, to_memory_id, relationship_type) VALUES (?, ?, ?)",
                [(memory.id, dep_id, "depends_on") for dep_id in memory.relationships.depends_on]
            )

        if memory.relationships.related_to:
            cursor.executemany(
                "INSERT INTO relationships (from_memory_id, to_memory_id, relationship_type) VALUES (?, ?, ?)",
                [(memory.id, rel_id, "related_to") for rel_id in memory.relationships.related_to]
            )

        self.conn.commit()

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
        cursor.execute("""
            SELECT * FROM memories
            WHERE id = ? AND repo_id = ? AND branch_name = ?
        """, (id, context.repo.id, context.branch.name))

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
            (row["id"], "depends_on")
        )
        depends_on = [r["to_memory_id"] for r in cursor.fetchall()]

        cursor.execute(
            "SELECT to_memory_id FROM relationships WHERE from_memory_id = ? AND relationship_type = ?",
            (row["id"], "related_to")
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
                line_range=(row["line_start"], row["line_end"]) if row["line_start"] is not None else None,
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
            conditions.append("repo_id = ?")
            params.append(context.repo.id)
            conditions.append("branch_name = ?")
            params.append(context.branch.name)
        elif filters.repo_id:
            conditions.append("repo_id = ?")
            params.append(filters.repo_id)

        # Other filters
        if filters.id:
            conditions.append("id = ?")
            params.append(filters.id)

        if filters.file_path:
            conditions.append("file_path LIKE ?")
            params.append(f"{filters.file_path}%")

        if filters.type:
            conditions.append("content_type = ?")
            params.append(filters.type)

        # Tag filter (requires join)
        query = "SELECT DISTINCT m.* FROM memories m"
        if filters.tags:
            query += " INNER JOIN tags t ON m.id = t.memory_id"
            placeholders = ",".join("?" * len(filters.tags))
            conditions.append(f"t.tag IN ({placeholders})")
            params.extend(filters.tags)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

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
            (id, context.repo.id, context.branch.name)
        )
        self.conn.commit()

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

    def close(self) -> None:
        """Close database connection."""
        self.conn.close()

    def get_connection(self) -> sqlite3.Connection:
        """Get database connection for advanced queries."""
        return self.conn
