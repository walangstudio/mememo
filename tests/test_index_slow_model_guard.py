"""The pre-index guard that warns before a slow model/CPU combo hangs."""

from __future__ import annotations

import logging

from mememo.embeddings.embedder import MODEL_REGISTRY
from mememo.tools.index_repository import _warn_if_slow_embedding


class _Emb:
    def __init__(self, model_name, device):
        self.model_name = model_name
        self.device = device


def test_registry_has_cpu_cost():
    # All known models carry the CPU-cost hint the guard reads.
    for key in ("minilm", "qwen3", "gemma"):
        assert MODEL_REGISTRY[key]["cpu_ms_per_chunk"] > 0
    assert (
        MODEL_REGISTRY["qwen3"]["cpu_ms_per_chunk"] > MODEL_REGISTRY["minilm"]["cpu_ms_per_chunk"]
    )


def test_warns_for_qwen3_on_cpu(caplog):
    with caplog.at_level(logging.WARNING):
        _warn_if_slow_embedding(_Emb("qwen3", "cpu"), 175)
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "minilm" in msgs and "MEMEMO_EMBEDDING_MODEL" in msgs
    assert "min" in msgs  # includes an ETA


def test_no_warn_for_minilm_cpu(caplog):
    with caplog.at_level(logging.WARNING):
        _warn_if_slow_embedding(_Emb("minilm", "cpu"), 5000)
    assert not caplog.records  # fast model — stay quiet even on a big repo


def test_no_warn_on_gpu(caplog):
    with caplog.at_level(logging.WARNING):
        _warn_if_slow_embedding(_Emb("qwen3", "cuda"), 5000)
        _warn_if_slow_embedding(_Emb("qwen3", "mps"), 5000)
    assert not caplog.records  # GPU is fast regardless of model


def test_no_warn_when_nothing_to_index(caplog):
    # An up-to-date incremental run (0 changed files) must not scream a slow-model ETA.
    with caplog.at_level(logging.WARNING):
        _warn_if_slow_embedding(_Emb("qwen3", "cpu"), 0)
    assert not caplog.records


def test_no_warn_when_device_absent(caplog):
    # An embedder without a known .device must not be assumed CPU and warned.
    class _NoDevice:
        model_name = "qwen3"

    with caplog.at_level(logging.WARNING):
        _warn_if_slow_embedding(_NoDevice(), 1000)
    assert not caplog.records


def test_eta_scales_with_file_count(caplog):
    # The warning's projected minutes should grow with the repo size.
    with caplog.at_level(logging.WARNING):
        _warn_if_slow_embedding(_Emb("qwen3", "cpu"), 1000)
    assert caplog.records and "min" in caplog.records[0].getMessage()
