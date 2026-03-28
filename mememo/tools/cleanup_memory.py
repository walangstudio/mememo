"""cleanup_memory tool - Manual, controlled memory cleanup."""

import logging
import time
from typing import TYPE_CHECKING

from .schemas import CleanupMemoryParams, CleanupMemoryResponse

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


async def cleanup_memory(
    params: CleanupMemoryParams, memory_manager: "MemoryManager"
) -> CleanupMemoryResponse:
    candidates: list[dict] = []
    seen_ids: set[str] = set()

    storage = memory_manager.storage_manager
    cursor = storage.conn.cursor()

    # Age-based cleanup
    if params.older_than_days and params.older_than_days > 0:
        cutoff = int(time.time()) - (params.older_than_days * 86400)
        if params.type:
            cursor.execute(
                "SELECT id, content_type, file_path FROM memories "
                "WHERE created_at < ? AND content_type = ?",
                (cutoff, params.type),
            )
        else:
            cursor.execute(
                "SELECT id, content_type, file_path FROM memories WHERE created_at < ?",
                (cutoff,),
            )
        for row in cursor.fetchall():
            candidates.append({
                "id": row[0],
                "type": row[1],
                "file_path": row[2] or "",
                "reason": f"older than {params.older_than_days} days",
            })
            seen_ids.add(row[0])

    # Stale-only cleanup
    if params.stale_only:
        cursor.execute(
            "SELECT id, content_type, file_path, stale_reason FROM memories WHERE stale = 1"
        )
        for row in cursor.fetchall():
            if row[0] not in seen_ids:
                candidates.append({
                    "id": row[0],
                    "type": row[1],
                    "file_path": row[2] or "",
                    "reason": f"stale: {row[3] or 'source changed'}",
                })
                seen_ids.add(row[0])

    # Dedup cleanup via checksum (exact duplicates)
    if params.dedup:
        cursor.execute(
            "SELECT checksum, GROUP_CONCAT(id, ',') as ids, COUNT(*) as cnt "
            "FROM (SELECT checksum, id, created_at FROM memories ORDER BY created_at ASC) "
            "GROUP BY checksum HAVING cnt > 1"
        )
        for row in cursor.fetchall():
            ids = row[1].split(",")
            # Keep the first (oldest), mark the rest as duplicates
            for dup_id in ids[1:]:
                if dup_id not in seen_ids:
                    candidates.append({
                        "id": dup_id,
                        "type": "",
                        "file_path": "",
                        "reason": f"exact duplicate (checksum={row[0][:12]}...)",
                    })
                    seen_ids.add(dup_id)

    if params.dry_run:
        return CleanupMemoryResponse(
            success=True,
            message=f"Dry run: {len(candidates)} memories would be deleted",
            candidates=candidates,
            deleted_count=0,
        )

    # Batch delete from SQLite
    ids_to_delete = [c["id"] for c in candidates]
    deleted = 0
    if ids_to_delete:
        try:
            placeholders = ",".join("?" * len(ids_to_delete))
            cursor.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", ids_to_delete)
            storage.conn.commit()
            deleted = cursor.rowcount
        except Exception as e:
            logger.warning("Batch delete failed: %s", e)
            storage.conn.rollback()
            return CleanupMemoryResponse(
                success=False,
                message=f"Delete failed: {e}",
                candidates=candidates,
                deleted_count=0,
            )

        # Clean up vector index entries (no batch API, but these are cheap in-memory ops)
        for mid in ids_to_delete:
            try:
                memory_manager.vector_index.delete_by_memory_id(mid)
            except Exception as e:
                logger.debug("Vector index cleanup failed for %s: %s", mid, e)

    return CleanupMemoryResponse(
        success=True,
        message=f"Deleted {deleted} of {len(candidates)} candidate memories",
        candidates=candidates,
        deleted_count=deleted,
    )
