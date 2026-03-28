"""Tests for intent classification via embedding centroids."""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from mememo.context.intent_classifier import (
    INTENT_PHRASES,
    INTENT_TYPE_PRIORITIES,
    IntentClassifier,
    IntentResult,
)


@pytest.fixture
def mock_embedder():
    embedder = MagicMock()
    embedder.model_name = "test-model"
    dim = 8
    # Return deterministic embeddings: hash-based so same phrase -> same vector
    def _embed_batch(texts):
        vecs = []
        for t in texts:
            rng = np.random.RandomState(hash(t) % 2**31)
            v = rng.randn(dim).astype(np.float32)
            v /= np.linalg.norm(v)
            vecs.append(v)
        return np.array(vecs)

    def _embed_query(text):
        return _embed_batch([text])[0]

    embedder.embed_batch = _embed_batch
    embedder.embed_query = _embed_query
    return embedder


@pytest.fixture
def cache_dir():
    d = Path(tempfile.mkdtemp())
    yield d
    shutil.rmtree(d)


def test_classify_returns_intent_result(mock_embedder, cache_dir):
    classifier = IntentClassifier(embedder=mock_embedder, cache_dir=cache_dir)
    prompt_emb = mock_embedder.embed_query("fix this bug please")
    result = classifier.classify(prompt_emb)
    assert isinstance(result, IntentResult)
    assert result.intent in INTENT_PHRASES
    assert 0.0 <= result.confidence <= 1.0


def test_classify_caches_centroids(mock_embedder, cache_dir):
    classifier = IntentClassifier(embedder=mock_embedder, cache_dir=cache_dir)
    prompt_emb = mock_embedder.embed_query("write a function")

    # First call computes centroids
    r1 = classifier.classify(prompt_emb)
    assert (cache_dir / "intent_centroids.npz").exists()
    assert (cache_dir / "intent_centroids_meta.json").exists()

    # Second call loads from cache (reset internal state)
    classifier2 = IntentClassifier(embedder=mock_embedder, cache_dir=cache_dir)
    r2 = classifier2.classify(prompt_emb)
    assert r1.intent == r2.intent
    assert abs(r1.confidence - r2.confidence) < 1e-6


def test_classify_regenerates_on_model_change(mock_embedder, cache_dir):
    classifier = IntentClassifier(embedder=mock_embedder, cache_dir=cache_dir)
    prompt_emb = mock_embedder.embed_query("test")
    classifier.classify(prompt_emb)

    # Change model name
    mock_embedder.model_name = "different-model"
    classifier2 = IntentClassifier(embedder=mock_embedder, cache_dir=cache_dir)
    assert not classifier2._cache_is_valid()


def test_low_confidence_falls_back_to_general(mock_embedder, cache_dir):
    classifier = IntentClassifier(embedder=mock_embedder, cache_dir=cache_dir)
    # Zero vector -> no similarity with any centroid
    zero_emb = np.zeros(8, dtype=np.float32)
    result = classifier.classify(zero_emb, confidence_threshold=0.3)
    assert result.intent == "general"
    assert result.confidence == 0.0


def test_get_type_priority():
    assert IntentClassifier.get_type_priority("debugging", "analysis") == 1.0
    assert IntentClassifier.get_type_priority("debugging", "code_snippet") == 0.7
    assert IntentClassifier.get_type_priority("debugging", "context") == 0.4
    assert IntentClassifier.get_type_priority("debugging", "conversation") == 0.2
    assert IntentClassifier.get_type_priority("debugging", "unknown_type") == 0.1
    # general intent
    assert IntentClassifier.get_type_priority("general", "context") == 1.0


def test_all_intents_have_priorities():
    for intent in INTENT_PHRASES:
        assert intent in INTENT_TYPE_PRIORITIES, f"Missing priority mapping for {intent}"
