"""Unit tests for mememo.embeddings.embedder."""

from __future__ import annotations

import pytest

pytest.importorskip("sentence_transformers")

from mememo.embeddings.embedder import MODEL_REGISTRY, Embedder  # noqa: E402


@pytest.mark.parametrize("model_name,expected_dim", [("minilm", 384), ("gemma", 768)])
def test_dimension_does_not_load_model(model_name: str, expected_dim: int) -> None:
    """Embedder.dimension must read from MODEL_REGISTRY without loading the
    SentenceTransformer — otherwise the MCP server pays a ~6s cold model load just
    to construct VectorIndex(dimension=embedder.dimension) at startup."""
    e = Embedder(model_name=model_name)
    assert e._model is None  # precondition: nothing loaded yet
    assert e.dimension == expected_dim
    assert e._model is None  # the property must not have triggered _load_model
    # And the cached registry value matches what the model would report:
    assert MODEL_REGISTRY[model_name]["dimension"] == expected_dim
