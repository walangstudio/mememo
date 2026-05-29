"""Wave 1C — reindex-identity command + FAISS dir move helper.

Exposes:
  move_vector_index(base_path, old_id, new_id) -> "moved" | "conflict" | "noop"
  move_faiss_dirs(base_path, manifest)          # called by server bg-thread
  reindex_identity(storage, base_path, dry_run) -> report dict
"""

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFLICT = "conflict"
_MOVED = "moved"
_NOOP = "noop"


def _contains_files(path: Path) -> bool:
    """True if any regular file lives anywhere under path (empty dirs ignored)."""
    return any(p.is_file() for p in path.rglob("*"))


def move_vector_index(base_path: Path, old_id: str, new_id: str) -> str:
    """Move vector_index/{old_id}/ -> vector_index/{new_id}/.

    Returns:
        "moved"    — move completed
        "conflict" — target exists and is non-empty; caller must clear
                     embedding_shard/embedding_index and re-embed
        "noop"     — old dir absent (nothing to move) or old_id == new_id
    """
    if old_id == new_id:
        return _NOOP

    src = Path(base_path) / old_id
    dst = Path(base_path) / new_id

    if not src.exists():
        return _NOOP

    if dst.exists():
        if _contains_files(dst):
            # Real FAISS data already lives under the new id. Don't clobber or
            # merge shards — caller clears embedding pointers and re-embeds.
            return _CONFLICT
        # Empty scaffolding only: VectorIndex.__init__ pre-creates
        # {new_id}/{branch}/ before the migration runs, so dst exists but holds
        # no shard files. Remove it so the move replaces it (a plain
        # shutil.move would otherwise nest src *inside* dst).
        shutil.rmtree(dst)

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return _MOVED


def move_faiss_dirs(base_path: Path, manifest: list[dict]) -> dict[str, int]:
    """Apply FAISS dir moves for every manifest entry that changed id.

    Called by the server bg-thread (_maybe_start_identity_migration) after
    _backfill_reindex_identity returns the manifest. Pure filesystem — never
    loads faiss. On conflict, clears embedding_shard/embedding_index via the
    storage connection embedded in each manifest entry (if present).

    Returns counts: {moved, conflict, noop, skipped}.
    """
    counts = {"moved": 0, "conflict": 0, "noop": 0, "skipped": 0}

    for entry in manifest:
        if entry.get("skipped") or entry["old_id"] == entry["new_id"]:
            counts["skipped"] += 1
            continue

        result = move_vector_index(
            base_path=base_path,
            old_id=entry["old_id"],
            new_id=entry["new_id"],
        )

        if result == _CONFLICT:
            counts["conflict"] += 1
            conn = entry.get("conn")
            if conn is not None:
                try:
                    conn.execute(
                        "UPDATE memories SET embedding_shard = NULL, embedding_index = NULL"
                        " WHERE repo_id = ?",
                        (entry["new_id"],),
                    )
                    conn.commit()
                except Exception as exc:
                    logger.warning(
                        "reindex: failed to clear embedding pointers for %s: %s",
                        entry["new_id"],
                        exc,
                    )
            logger.warning(
                "reindex: FAISS conflict for %s -> %s; embedding pointers cleared for re-embed",
                entry["old_id"],
                entry["new_id"],
            )
        elif result == _MOVED:
            counts["moved"] += 1
            logger.info(
                "reindex: moved vector_index/%s -> vector_index/%s",
                entry["old_id"],
                entry["new_id"],
            )
        else:
            counts["noop"] += 1

    return counts


def reindex_identity(storage, base_path: Path, dry_run: bool = False) -> dict:
    """Re-derive repo_ids via live-git resolver and move FAISS dirs to match.

    Builds a resolver callable using git remote + project_config, calls
    storage._backfill_reindex_identity(resolver, dry_run), then applies FAISS
    dir moves via move_faiss_dirs.

    Args:
        storage:   StorageManager instance (has .conn, .base_dir,
                   ._backfill_reindex_identity).
        base_path: base directory for vector_index shards
                   (usually storage.base_dir / "vector_index").
        dry_run:   when True, return manifest without mutating DB or fs.

    Returns a report dict:
        {
          "manifest": list[dict],   # raw backfill manifest
          "moves":    int,          # FAISS dirs actually moved
          "conflicts": int,
          "noops":    int,
          "skipped":  int,
        }
    """
    import asyncio

    from ..core.git_manager import GitManager
    from ..core.identity import resolve_project_id
    from ..core.project_config import load_project_config

    _git = GitManager()

    def _resolver(repo_path, remote_url):
        loop = asyncio.new_event_loop()
        try:
            remote = loop.run_until_complete(_git.get_remote_url(repo_path))
        finally:
            loop.close()
        return resolve_project_id(
            repo_path=repo_path,
            remote_url=remote or remote_url,
            project_config=load_project_config(repo_path),
        )

    manifest = storage._backfill_reindex_identity(_resolver, dry_run=dry_run)

    if dry_run:
        return {
            "manifest": manifest,
            "moves": 0,
            "conflicts": 0,
            "noops": 0,
            "skipped": len(manifest),
            "dry_run": True,
        }

    # Attach conn so move_faiss_dirs can clear embedding pointers on conflict.
    for entry in manifest:
        entry["conn"] = storage.conn

    move_counts = move_faiss_dirs(base_path=Path(base_path), manifest=manifest)

    return {
        "manifest": manifest,
        "moves": move_counts["moved"],
        "conflicts": move_counts["conflict"],
        "noops": move_counts["noop"],
        "skipped": move_counts["skipped"],
        "dry_run": False,
    }
