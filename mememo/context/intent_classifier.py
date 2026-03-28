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
        "write function",
        "implement feature",
        "add method",
        "create class",
        "build component",
        "code this",
        "generate code",
        "refactor this function",
    ],
    "debugging": [
        "fix bug",
        "error traceback",
        "why is this failing",
        "stack trace",
        "debug issue",
        "not working",
        "exception thrown",
        "unexpected behavior",
    ],
    "architecture": [
        "design system",
        "which pattern",
        "database choice",
        "refactor architecture",
        "system design",
        "tradeoffs between",
        "scalability concern",
        "microservice vs monolith",
    ],
    "testing": [
        "write test",
        "test coverage",
        "assert that",
        "mock dependency",
        "unit test",
        "integration test",
        "test case for",
        "pytest fixture",
    ],
    "review": [
        "review code",
        "pull request",
        "code quality",
        "review diff",
        "check this code",
        "code smell",
        "best practice",
        "improve readability",
    ],
    "general": [
        "explain this",
        "what does this do",
        "how does this work",
        "summarize",
        "describe the",
        "help me understand",
        "overview of",
        "tell me about",
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

    def classify(self, prompt_embedding: np.ndarray, confidence_threshold: float = 0.3) -> IntentResult:
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
