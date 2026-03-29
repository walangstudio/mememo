"""
Embedding-based intent classification for user prompts.

Classifies user intent by cosine similarity against pre-computed
intent centroids. No LLM call required -- uses the same embedder
already initialized for memory search.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..embeddings.embedder import Embedder

logger = logging.getLogger(__name__)

INTENT_PHRASES: dict[str, list[str]] = {
    "coding": [
        "write a function that handles user authentication",
        "implement the CRUD endpoints for the projects resource",
        "add a new method to the storage manager class",
        "create a class for managing database connections",
        "build a component that displays the search results",
        "code the API handler for file uploads",
        "generate the boilerplate for a new MCP tool",
        "refactor this function to use async await instead of callbacks",
    ],
    "debugging": [
        "fix the bug where login fails with invalid token error",
        "I'm getting a traceback error when running the indexer",
        "why is the search returning stale results after branch switch",
        "there's a stack trace in the logs about memory allocation failure",
        "debug the issue with vector index not loading shards correctly",
        "the hook injection is not working and returns empty results",
        "an exception is thrown when the database connection times out",
        "unexpected behavior when two processes write to the same file",
    ],
    "architecture": [
        "should we use PostgreSQL or SQLite for the metadata storage",
        "which design pattern would work best for the plugin system",
        "help me decide between REST and GraphQL for this API",
        "how should we architect the caching layer for better scalability",
        "what are the tradeoffs between microservices and monolith here",
        "design the data flow for the real-time notification system",
        "evaluate whether we should use event sourcing for audit logs",
        "plan the migration strategy from the legacy storage backend",
    ],
    "testing": [
        "write unit tests for the memory manager create function",
        "we need better test coverage for the vector index module",
        "add assertions to verify the search results are ranked correctly",
        "mock the database dependency in the storage manager tests",
        "create integration tests for the full capture pipeline",
        "the test suite needs end-to-end tests for the hook system",
        "write a test case for the edge case when embedder returns zero vector",
        "add pytest fixtures for the common test setup with temp directories",
    ],
    "review": [
        "review the pull request for the new cleanup memory feature",
        "check this code for potential security vulnerabilities",
        "review the diff for the config changes and flag any issues",
        "look at the recent changes and identify any code smells",
        "review whether this implementation follows our coding standards",
        "check if the error handling in this module is consistent",
        "review the API design for the skill management endpoints",
        "evaluate the code quality of the new context selection module",
    ],
    "general": [
        "explain how the hook injection system works in mememo",
        "what does the vector index sharding mechanism do exactly",
        "how does the incremental indexing detect changed files",
        "give me a summary of the recent changes to the codebase",
        "describe the relationship between storage manager and memory manager",
        "help me understand the embedding model selection tradeoffs",
        "provide an overview of the mememo architecture",
        "tell me about the different memory types and when to use each",
    ],
}

# Memory type priorities per intent (ordered by priority)
INTENT_TYPE_PRIORITIES: dict[str, list[str]] = {
    "coding": ["code_snippet", "context", "decision", "relationship"],
    "debugging": ["analysis", "code_snippet", "context", "conversation"],
    "architecture": ["decision", "analysis", "context", "summary"],
    "testing": ["code_snippet", "analysis", "context", "decision"],
    "review": ["code_snippet", "decision", "context", "analysis"],
    "general": ["context", "decision", "conversation", "summary"],
}


@dataclass
class IntentResult:
    intent: str
    confidence: float


class IntentClassifier:
    def __init__(self, embedder: Embedder, cache_dir: Path):
        self._embedder = embedder
        self._cache_dir = cache_dir
        self._centroids: dict[str, np.ndarray] | None = None
        self._cache_file = cache_dir / "intent_centroids.npz"
        self._meta_file = cache_dir / "intent_centroids_meta.json"

    def _cache_is_valid(self) -> bool:
        if not self._cache_file.exists() or not self._meta_file.exists():
            return False
        try:
            meta = json.loads(self._meta_file.read_text())
            return meta.get("model") == self._embedder.model_name
        except (json.JSONDecodeError, OSError):
            return False

    def _load_or_compute_centroids(self) -> dict[str, np.ndarray]:
        if self._centroids is not None:
            return self._centroids

        if self._cache_is_valid():
            data = np.load(self._cache_file)
            self._centroids = {name: data[name] for name in data.files}
            logger.debug("Loaded cached intent centroids for model=%s", self._embedder.model_name)
            return self._centroids

        logger.info("Computing intent centroids for model=%s", self._embedder.model_name)
        self._centroids = {}
        for intent, phrases in INTENT_PHRASES.items():
            embeddings = self._embedder.embed_batch(phrases)
            centroid = np.mean(embeddings, axis=0)
            norm = np.linalg.norm(centroid)
            if norm > 0:
                centroid = centroid / norm
            self._centroids[intent] = centroid

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        np.savez(self._cache_file, **self._centroids)
        self._meta_file.write_text(json.dumps({"model": self._embedder.model_name}))
        logger.info("Cached intent centroids to %s", self._cache_file)

        return self._centroids

    def classify(
        self, prompt_embedding: np.ndarray, confidence_threshold: float = 0.3
    ) -> IntentResult:
        centroids = self._load_or_compute_centroids()

        # Normalize prompt embedding
        norm = np.linalg.norm(prompt_embedding)
        if norm > 0:
            prompt_norm = prompt_embedding / norm
        else:
            return IntentResult(intent="general", confidence=0.0)

        best_intent = "general"
        best_score = -1.0

        for intent, centroid in centroids.items():
            score = float(np.dot(prompt_norm, centroid))
            if score > best_score:
                best_score = score
                best_intent = intent

        # Clamp to [0, 1] — cosine similarity can be negative with dissimilar vectors
        best_score = max(0.0, min(1.0, best_score))

        if best_score < confidence_threshold:
            return IntentResult(intent="general", confidence=best_score)

        return IntentResult(intent=best_intent, confidence=best_score)

    @staticmethod
    def get_type_priority(intent: str, memory_type: str) -> float:
        priorities = INTENT_TYPE_PRIORITIES.get(intent, INTENT_TYPE_PRIORITIES["general"])
        try:
            idx = priorities.index(memory_type)
            return [1.0, 0.7, 0.4, 0.2][idx]
        except (ValueError, IndexError):
            return 0.1
