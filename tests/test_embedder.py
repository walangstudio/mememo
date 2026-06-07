"""Unit tests for mememo.embeddings.embedder."""

from __future__ import annotations

import pytest

pytest.importorskip("sentence_transformers")

import mememo.embeddings.embedder as emb  # noqa: E402
from mememo.embeddings.embedder import MODEL_REGISTRY, Embedder  # noqa: E402


@pytest.mark.parametrize(
    "model_name,expected_dim", [("minilm", 384), ("qwen3", 1024), ("gemma", 768)]
)
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


class _FakeModel:
    """Records encode kwargs so tests can assert query-prompt routing without
    downloading a real SentenceTransformer."""

    def __init__(self, prompts: dict) -> None:
        self.prompts = prompts
        self.calls: list[dict] = []

    def encode(self, texts, **kwargs):
        import numpy as np

        self.calls.append(kwargs)
        return np.zeros((len(texts), 8), dtype="float32")

    def get_sentence_embedding_dimension(self) -> int:
        return 8


def test_embed_query_applies_prompt_name_for_asymmetric_model() -> None:
    """Qwen3 is asymmetric: queries must be encoded with prompt_name='query'."""
    e = Embedder(model_name="qwen3")
    e._model = _FakeModel({"query": "Instruct: ...\nQuery:"})
    e.embed_query("anti-zip archiver")
    assert e._model.calls[-1].get("prompt_name") == "query"


def test_embed_query_no_prompt_for_symmetric_model() -> None:
    """MiniLM is symmetric: no query prompt is applied."""
    e = Embedder(model_name="minilm")
    e._model = _FakeModel({})
    e.embed_query("anti-zip archiver")
    assert "prompt_name" not in e._model.calls[-1]


def test_embed_query_degrades_when_model_lacks_declared_prompt() -> None:
    """A registry query_prompt_name the loaded model doesn't define must fall back
    to a bare encode, never raise."""
    e = Embedder(model_name="qwen3")
    e._model = _FakeModel({})  # model unexpectedly has no 'query' prompt
    e.embed_query("anti-zip archiver")  # must not raise
    assert "prompt_name" not in e._model.calls[-1]


def test_embed_documents_never_apply_query_prompt() -> None:
    """embed()/embed_batch() encode stored documents — they must not get the query
    instruction even for an asymmetric model, or query/document sides misalign."""
    e = Embedder(model_name="qwen3")
    e._model = _FakeModel({"query": "Instruct: ...\nQuery:"})
    e.embed(["stored content"])
    assert "prompt_name" not in e._model.calls[-1]


def _reset_ca_flag() -> None:
    emb._SYSTEM_CA_READY = False


def test_ensure_system_ca_opt_out_skips_injection(monkeypatch) -> None:
    """MEMEMO_USE_SYSTEM_CA=0 must not touch SSL (keep the stock CA bundle)."""
    _reset_ca_flag()
    monkeypatch.setenv("MEMEMO_USE_SYSTEM_CA", "0")
    called = {"inject": False}
    import truststore

    monkeypatch.setattr(truststore, "inject_into_ssl", lambda: called.__setitem__("inject", True))
    emb._ensure_system_ca()
    assert called["inject"] is False
    # Opting out must NOT consume the one-shot flag: a later enabled call in the
    # same process must still be able to inject.
    assert emb._SYSTEM_CA_READY is False
    monkeypatch.setenv("MEMEMO_USE_SYSTEM_CA", "1")
    emb._ensure_system_ca()
    assert called["inject"] is True


def test_ensure_system_ca_injects_once(monkeypatch) -> None:
    """Default path injects the OS trust store exactly once (idempotent)."""
    _reset_ca_flag()
    monkeypatch.delenv("MEMEMO_USE_SYSTEM_CA", raising=False)
    count = {"n": 0}
    import truststore

    monkeypatch.setattr(
        truststore, "inject_into_ssl", lambda: count.__setitem__("n", count["n"] + 1)
    )
    emb._ensure_system_ca()
    emb._ensure_system_ca()  # second call is a no-op (guard flag)
    assert count["n"] == 1


def test_ensure_system_ca_never_raises(monkeypatch) -> None:
    """A failed injection must degrade quietly, not break model loading."""
    _reset_ca_flag()
    monkeypatch.delenv("MEMEMO_USE_SYSTEM_CA", raising=False)
    import truststore

    def _boom() -> None:
        raise RuntimeError("no OS trust store here")

    monkeypatch.setattr(truststore, "inject_into_ssl", _boom)
    emb._ensure_system_ca()  # must not raise


def teardown_module(_module) -> None:
    # Don't leave the one-shot guard tripped for the rest of the suite.
    emb._SYSTEM_CA_READY = False
