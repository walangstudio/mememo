"""Unit tests for mememo.embeddings.embedder."""

from __future__ import annotations

import pytest

pytest.importorskip("sentence_transformers")

import mememo.embeddings.embedder as emb  # noqa: E402
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
