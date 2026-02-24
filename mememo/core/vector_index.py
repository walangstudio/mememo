"""
FAISS vector index management with automatic sharding.

Handles large-scale vector search with lazy loading and LRU eviction.
"""

import logging
import sqlite3
import time
from pathlib import Path

import faiss
import numpy as np

logger = logging.getLogger(__name__)


class VectorIndex:
    """
    Manages FAISS indices with automatic sharding for large codebases.

    Features:
    - Automatic sharding at 50k vectors per shard
    - Lazy loading: only active indices kept in memory
    - LRU eviction after 5 minutes of inactivity
    - SQLite for vector→memory mappings
    """

    SHARD_SIZE = 50_000
    INACTIVE_THRESHOLD = 300  # 5 minutes in seconds

    def __init__(
        self,
        base_path: Path,
        repo_id: str,
        branch: str,
        dimension: int = 384,
    ):
        """
        Initialize vector index.

        Args:
            base_path: Base directory for all indices
            repo_id: Repository ID
            branch: Branch name
            dimension: Embedding dimension (384 for MiniLM, 768 for Gemma)
        """
        self.base_path = Path(base_path)
        self.repo_id = repo_id
        self.branch = branch
        self.dimension = dimension

        # Index directory for this repo/branch
        self.index_dir = self.base_path / repo_id / branch
        self.index_dir.mkdir(parents=True, exist_ok=True)

        # SQLite for vector→memory mappings
        self.db_path = self.index_dir / "mappings.db"
        self._init_mappings_db()

        # In-memory cache of loaded shards (shard_id → (index, last_access_time))
        self.loaded_shards: dict[int, tuple[faiss.Index, float]] = {}
        self.current_shard = self._get_current_shard()

        logger.info(
            f"VectorIndex initialized: repo={repo_id}, branch={branch}, "
            f"dimension={dimension}, current_shard={self.current_shard}"
        )

    def _init_mappings_db(self) -> None:
        """Initialize SQLite database for vector mappings."""
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS vector_mappings (
                shard_id INTEGER NOT NULL,
                local_index INTEGER NOT NULL,
                global_index INTEGER PRIMARY KEY,
                memory_id TEXT NOT NULL,
                checksum TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_id ON vector_mappings(memory_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_shard ON vector_mappings(shard_id)")
        conn.commit()
        conn.close()

    def _get_current_shard(self) -> int:
        """
        Get current shard ID for new vectors.

        Returns:
            Current shard ID
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.execute("SELECT MAX(shard_id) FROM vector_mappings")
        result = cursor.fetchone()[0]
        conn.close()

        if result is None:
            return 0

        # Check if current shard is full
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.execute("SELECT COUNT(*) FROM vector_mappings WHERE shard_id = ?", (result,))
        count = cursor.fetchone()[0]
        conn.close()

        return result if count < self.SHARD_SIZE else result + 1

    def _load_shard(self, shard_id: int) -> faiss.Index:
        """
        Load FAISS index shard from disk (lazy loading).

        Args:
            shard_id: Shard ID to load

        Returns:
            Loaded FAISS index
        """
        # Check if already loaded
        if shard_id in self.loaded_shards:
            index, _ = self.loaded_shards[shard_id]
            # Update access time (LRU)
            self.loaded_shards[shard_id] = (index, time.time())
            return index

        shard_path = self.index_dir / f"shard_{shard_id}.faiss"

        # Load existing index or create new one
        if shard_path.exists():
            logger.debug(f"Loading shard {shard_id} from {shard_path}")
            index = faiss.read_index(str(shard_path))
        else:
            logger.debug(f"Creating new shard {shard_id}")
            index = faiss.IndexFlatL2(self.dimension)

        # Cache in memory
        self.loaded_shards[shard_id] = (index, time.time())

        # Evict inactive shards
        self._evict_inactive_shards()

        return index

    def _evict_inactive_shards(self) -> None:
        """Evict shards inactive for >5 minutes to free memory."""
        current_time = time.time()
        to_evict = []

        for shard_id, (index, last_access) in self.loaded_shards.items():
            if current_time - last_access > self.INACTIVE_THRESHOLD:
                # Save to disk before evicting
                shard_path = self.index_dir / f"shard_{shard_id}.faiss"
                faiss.write_index(index, str(shard_path))
                to_evict.append(shard_id)
                logger.debug(f"Evicting inactive shard {shard_id}")

        for shard_id in to_evict:
            del self.loaded_shards[shard_id]

    def add(
        self,
        embeddings: list[list[float]],
        memory_ids: list[str],
        checksums: list[str],
    ) -> None:
        """
        Add vectors to index with automatic sharding.

        Args:
            embeddings: List of embedding vectors
            memory_ids: Corresponding memory IDs
            checksums: Content checksums for deduplication
        """
        if len(embeddings) != len(memory_ids) or len(embeddings) != len(checksums):
            raise ValueError("embeddings, memory_ids, and checksums must have same length")

        np.array(embeddings, dtype="float32")

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # Get current global index
        cursor.execute("SELECT COALESCE(MAX(global_index), -1) + 1 FROM vector_mappings")
        global_index = cursor.fetchone()[0]

        for i, (embedding, memory_id, checksum) in enumerate(
            zip(embeddings, memory_ids, checksums)
        ):
            # Check if current shard is full
            cursor.execute(
                "SELECT COUNT(*) FROM vector_mappings WHERE shard_id = ?", (self.current_shard,)
            )
            shard_count = cursor.fetchone()[0]

            if shard_count >= self.SHARD_SIZE:
                # Save current shard and move to next
                logger.info(f"Shard {self.current_shard} full, creating new shard")
                current_index = self._load_shard(self.current_shard)
                shard_path = self.index_dir / f"shard_{self.current_shard}.faiss"
                faiss.write_index(current_index, str(shard_path))
                self.current_shard += 1

            # Load current shard index
            index = self._load_shard(self.current_shard)

            # Get local index within shard
            cursor.execute(
                "SELECT COUNT(*) FROM vector_mappings WHERE shard_id = ?", (self.current_shard,)
            )
            local_index = cursor.fetchone()[0]

            # Add to FAISS index
            index.add(np.array([embedding], dtype="float32"))

            # Add mapping to SQLite
            cursor.execute(
                """
                INSERT INTO vector_mappings
                (shard_id, local_index, global_index, memory_id, checksum, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    self.current_shard,
                    local_index,
                    global_index + i,
                    memory_id,
                    checksum,
                    int(time.time()),
                ),
            )

        conn.commit()
        conn.close()

        logger.info(f"Added {len(embeddings)} vectors to index")

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
    ) -> tuple[list[float], list[str]]:
        """
        Search across all shards for similar vectors.

        Args:
            query_embedding: Query vector
            top_k: Number of results to return

        Returns:
            Tuple of (distances, memory_ids)
        """
        query = np.array([query_embedding], dtype="float32")

        # Get all shard IDs
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.execute("SELECT DISTINCT shard_id FROM vector_mappings ORDER BY shard_id")
        shard_ids = [row[0] for row in cursor.fetchall()]
        conn.close()

        if not shard_ids:
            return [], []

        # Search each shard and collect results
        all_distances = []
        all_memory_ids = []

        for shard_id in shard_ids:
            index = self._load_shard(shard_id)

            # Search this shard
            k = min(top_k, index.ntotal)
            if k == 0:
                continue

            distances, indices = index.search(query, k)

            # Map local indices to memory IDs
            conn = sqlite3.connect(str(self.db_path))
            for local_idx, dist in zip(indices[0], distances[0]):
                if local_idx == -1:  # FAISS returns -1 for missing results
                    continue
                cursor = conn.execute(
                    """
                    SELECT memory_id FROM vector_mappings
                    WHERE shard_id = ? AND local_index = ?
                """,
                    (shard_id, int(local_idx)),
                )
                result = cursor.fetchone()
                if result:
                    all_distances.append(float(dist))
                    all_memory_ids.append(result[0])
            conn.close()

        # Sort by distance and return top_k
        sorted_pairs = sorted(zip(all_distances, all_memory_ids), key=lambda x: x[0])
        sorted_pairs = sorted_pairs[:top_k]

        if not sorted_pairs:
            return [], []

        distances, memory_ids = zip(*sorted_pairs)
        return list(distances), list(memory_ids)

    def delete_by_memory_id(self, memory_id: str) -> None:
        """
        Delete vector by memory ID.

        Note: FAISS doesn't support efficient deletion, so we mark in SQLite
        and rebuild index periodically.

        Args:
            memory_id: Memory ID to delete
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("DELETE FROM vector_mappings WHERE memory_id = ?", (memory_id,))
        conn.commit()
        conn.close()

        logger.info(f"Deleted vector for memory {memory_id}")

    def get_statistics(self) -> dict:
        """
        Get index statistics.

        Returns:
            Dict with statistics
        """
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.execute("SELECT COUNT(DISTINCT shard_id), COUNT(*) FROM vector_mappings")
        shard_count, total_vectors = cursor.fetchone()
        conn.close()

        return {
            "total_vectors": total_vectors or 0,
            "shard_count": shard_count or 0,
            "loaded_shards": len(self.loaded_shards),
            "active_shards": len(self.loaded_shards),  # Same as loaded_shards (shards in memory)
            "total_shards": shard_count or 0,  # Same as shard_count (total shards on disk)
            "vectors_per_shard": self.SHARD_SIZE,
            "dimension": self.dimension,
        }

    def close(self) -> None:
        """Save all loaded shards and close."""
        for shard_id, (index, _) in self.loaded_shards.items():
            shard_path = self.index_dir / f"shard_{shard_id}.faiss"
            faiss.write_index(index, str(shard_path))
            logger.debug(f"Saved shard {shard_id}")

        self.loaded_shards.clear()
