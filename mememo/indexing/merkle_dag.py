"""
Merkle DAG for incremental indexing.

Tracks file changes using SHA-256 hashing to detect:
- New files (not in DAG)
- Modified files (hash changed)
- Unchanged files (hash same)

This enables incremental indexing - only re-index changed files.
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Dict, List, Set

logger = logging.getLogger(__name__)


class MerkleDAG:
    """
    Merkle DAG for file change detection.

    Stores SHA-256 hashes of files to detect changes between indexing runs.
    """

    def __init__(self, storage_path: Path):
        """
        Initialize Merkle DAG.

        Args:
            storage_path: Path to store DAG data
        """
        self.storage_path = storage_path
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.hashes_file = self.storage_path / "file_hashes.json"
        self.hashes: Dict[str, str] = self._load_hashes()

    def _load_hashes(self) -> Dict[str, str]:
        """
        Load file hashes from disk.

        Returns:
            Dict mapping file paths to SHA-256 hashes
        """
        if not self.hashes_file.exists():
            logger.debug("No existing hashes file, starting fresh")
            return {}

        try:
            with open(self.hashes_file, "r", encoding="utf-8") as f:
                hashes = json.load(f)
            logger.debug(f"Loaded {len(hashes)} file hashes from {self.hashes_file}")
            return hashes
        except Exception as e:
            logger.warning(f"Failed to load hashes file: {e}, starting fresh")
            return {}

    def _save_hashes(self):
        """Save file hashes to disk."""
        try:
            with open(self.hashes_file, "w", encoding="utf-8") as f:
                json.dump(self.hashes, f, indent=2)
            logger.debug(f"Saved {len(self.hashes)} file hashes to {self.hashes_file}")
        except Exception as e:
            logger.error(f"Failed to save hashes file: {e}")

    def compute_file_hash(self, file_path: Path) -> str:
        """
        Compute SHA-256 hash of file content.

        Args:
            file_path: Path to file

        Returns:
            SHA-256 hash as hex string
        """
        try:
            content = file_path.read_bytes()
            return hashlib.sha256(content).hexdigest()
        except Exception as e:
            logger.warning(f"Failed to hash {file_path}: {e}")
            # Return empty hash on error (will be treated as changed)
            return ""

    def get_changed_files(self, file_paths: List[Path]) -> Set[Path]:
        """
        Detect which files have changed since last indexing.

        Args:
            file_paths: List of file paths to check

        Returns:
            Set of changed file paths (new or modified)
        """
        changed = set()

        for file_path in file_paths:
            # Compute current hash
            current_hash = self.compute_file_hash(file_path)
            file_key = str(file_path)

            # Check if file is new or changed
            if file_key not in self.hashes or self.hashes[file_key] != current_hash:
                changed.add(file_path)
                # Update hash
                self.hashes[file_key] = current_hash

        # Save updated hashes
        self._save_hashes()

        logger.info(
            f"Change detection: {len(changed)} changed, {len(file_paths) - len(changed)} unchanged"
        )
        return changed

    def mark_file_indexed(self, file_path: Path):
        """
        Mark a file as indexed (update its hash).

        Args:
            file_path: Path to file
        """
        current_hash = self.compute_file_hash(file_path)
        self.hashes[str(file_path)] = current_hash
        self._save_hashes()

    def clear(self):
        """Clear all stored hashes (force re-index)."""
        self.hashes = {}
        self._save_hashes()
        logger.info("Cleared Merkle DAG (force re-index)")

    def get_statistics(self) -> dict:
        """
        Get Merkle DAG statistics.

        Returns:
            Dict with statistics
        """
        return {
            "total_tracked_files": len(self.hashes),
            "storage_path": str(self.storage_path),
        }
