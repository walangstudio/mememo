"""
Incremental indexing for mememo.

Merkle DAG-based change detection for efficient re-indexing.
"""

from .merkle_dag import MerkleDAG

__all__ = ["MerkleDAG"]
