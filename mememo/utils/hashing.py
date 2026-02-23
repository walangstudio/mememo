"""
Hashing utilities for mememo.

Provides SHA-256 hashing for content deduplication and path identification.
"""

import hashlib


def calculate_checksum(content: str) -> str:
    """
    Calculate SHA-256 hash of content.

    Used for deduplication and integrity checking.

    Args:
        content: Text content to hash

    Returns:
        Hex-encoded SHA-256 hash
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def hash_path(filepath: str) -> str:
    """
    Calculate SHA-256 hash of a file path.

    Used to generate stable repo IDs.

    Args:
        filepath: File or directory path

    Returns:
        First 16 characters of SHA-256 hash (for readability)
    """
    full_hash = hashlib.sha256(filepath.encode("utf-8")).hexdigest()
    return full_hash[:16]  # Use first 16 chars for readability
