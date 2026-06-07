"""
Embedding generation for mememo.

Provides local, offline embedding generation with multiple model support.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)


# Model registry.
#
# query_prompt_name: the sentence-transformers prompt to apply to *query* text
# (asymmetric models prepend an instruction to queries but embed documents bare).
# None → symmetric model, encode queries and documents identically. The prompt is
# resolved from the model's own ST config at encode time; if the loaded model
# doesn't actually define it, embed_query() falls back to a bare encode rather
# than raising, so a wrong registry hint degrades instead of crashing.
MODEL_REGISTRY = {
    "minilm": {
        "name": "sentence-transformers/all-MiniLM-L6-v2",
        "dimension": 384,
        "size_mb": 90,
        "query_prompt_name": None,
        "description": "Lightweight, fast, good quality (default)",
    },
    "qwen3": {
        "name": "Qwen/Qwen3-Embedding-0.6B",
        "dimension": 1024,
        "size_mb": 1200,
        "query_prompt_name": "query",
        "description": (
            "Highest quality, Apache-2.0, instruction-aware (1024-dim, 32k ctx). "
            "Switching to it requires a re-index."
        ),
    },
    "gemma": {
        "name": "google/embeddinggemma-300m",
        "dimension": 768,
        "size_mb": 1200,
        "query_prompt_name": None,
        "description": "768-dim, experimental; gated download (requires HuggingFace login)",
    },
}

DeviceType = Literal["auto", "cpu", "cuda", "mps"]
ModelName = Literal["minilm", "qwen3", "gemma"]

_SYSTEM_CA_READY = False


def _ensure_system_ca() -> None:
    """Route SSL verification through the OS trust store before the model download.

    On a TLS-intercepting corporate proxy the standard certifi bundle doesn't
    contain the proxy's root CA, so the first-run HuggingFace download fails with
    ``CERTIFICATE_VERIFY_FAILED`` and mememo can't build an index. The OS trust
    store *does* hold that CA (it's why browsers work), so ``truststore`` makes
    the download succeed — the Python analog of curl/Node's ``--use-system-ca``.

    Best-effort and idempotent: runs once, never raises (a missing truststore or
    a failed injection just leaves the default certifi bundle in place). Opt out
    with ``MEMEMO_USE_SYSTEM_CA=0`` to keep the stock CA bundle.
    """
    global _SYSTEM_CA_READY
    if _SYSTEM_CA_READY:
        return

    import os

    if os.environ.get("MEMEMO_USE_SYSTEM_CA", "1").strip().lower() in ("0", "false", "no"):
        return  # opted out — leave the one-shot flag unset so it stays a no-op

    try:
        import truststore

        truststore.inject_into_ssl()
        logger.debug("truststore: SSL verification now uses the OS trust store")
    except Exception as e:  # truststore absent or injection failed — degrade quietly
        logger.debug("truststore unavailable (%s); using the default CA bundle", e)
    finally:
        # Set the flag only *after* the attempt completes. inject_into_ssl is
        # idempotent, so if a concurrent first-run load races in before this, it
        # re-injects harmlessly rather than skipping injection that hasn't taken
        # effect yet and then downloading with the stock CA bundle.
        _SYSTEM_CA_READY = True


class Embedder:
    """
    Unified embedder supporting multiple models with device auto-detection.

    Features:
    - Multiple embedding models (MiniLM, EmbeddingGemma)
    - Device auto-detection (CUDA → MPS → CPU)
    - Batch processing
    - Lazy loading
    """

    def __init__(
        self,
        model_name: ModelName = "minilm",
        device: DeviceType = "auto",
        batch_size: int = 32,
    ):
        """
        Initialize embedder.

        Args:
            model_name: Model to use ("minilm", "qwen3", or "gemma")
            device: Device to use ("auto", "cpu", "cuda", "mps")
            batch_size: Batch size for encoding
        """
        self.model_name = model_name
        self.batch_size = batch_size
        self._model = None
        self._dimension = None
        self._warned_missing_prompt = False

        # Auto-detect device
        if device == "auto":
            device = self._detect_device()

        self.device = device
        logger.info(f"Embedder initialized: model={model_name}, device={device}")

    def _detect_device(self) -> str:
        """
        Auto-detect best available device.

        Returns:
            Device name: "cuda", "mps", or "cpu"
        """
        try:
            import torch

            if torch.cuda.is_available():
                logger.info("CUDA detected - using GPU acceleration")
                return "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                logger.info("MPS detected - using Apple Silicon acceleration")
                return "mps"
            else:
                logger.info("Using CPU (no GPU detected)")
                return "cpu"
        except ImportError:
            logger.warning("PyTorch not available, falling back to CPU")
            return "cpu"

    @property
    def model(self) -> SentenceTransformer:
        """
        Get or load the model (lazy loading).

        Returns:
            Loaded SentenceTransformer model
        """
        if self._model is None:
            self._load_model()
        return self._model

    def _load_model(self) -> None:
        """Load the embedding model."""
        # Deferred import: sentence_transformers pulls torch (~seconds, cold).
        # Keep it out of module import so the MCP stdio handshake stays fast.
        from sentence_transformers import SentenceTransformer

        if self.model_name not in MODEL_REGISTRY:
            raise ValueError(
                f"Unknown model: {self.model_name}. " f"Available: {list(MODEL_REGISTRY.keys())}"
            )

        model_info = MODEL_REGISTRY[self.model_name]
        logger.info(f"Loading embedding model: {model_info['name']}")
        logger.info(f"  Dimension: {model_info['dimension']}")
        logger.info(f"  Size: ~{model_info['size_mb']} MB")

        # Load model with device. Try cache-only first: a cached model must not
        # trigger a network adapter-config probe (huggingface_hub + httpx raise
        # "client has been closed" on that path). Fall back to a real download
        # only when the model isn't cached yet.
        try:
            self._model = SentenceTransformer(
                model_info["name"],
                device=self.device,
                local_files_only=True,
            )
        except Exception as e:
            logger.warning(
                f"Cache-only load failed ({type(e).__name__}: {e}); "
                f"falling back to download for {model_info['name']}"
            )
            # Only now, when a network download is actually required, route SSL
            # through the OS trust store so the download works behind a corporate
            # TLS proxy. Cached / offline loads never reach here, so the
            # process-wide SSL change is scoped to genuine first-run downloads.
            _ensure_system_ca()
            self._model = SentenceTransformer(
                model_info["name"],
                device=self.device,
            )

        self._dimension = self._model.get_sentence_embedding_dimension()
        logger.info(f"Model loaded successfully. Dimension: {self._dimension}")

    @property
    def dimension(self) -> int:
        """
        Get embedding dimension.

        Returns:
            Embedding dimension
        """
        if self._dimension is None:
            # Prefer the registry: every known model already publishes its dimension,
            # so VectorIndex(..., dimension=embedder.dimension) at server start doesn't
            # have to load the SentenceTransformer (~6s cold on Windows) just to read
            # a number. Unknown models fall through to a real model load.
            info = MODEL_REGISTRY.get(self.model_name)
            if info and "dimension" in info:
                self._dimension = info["dimension"]
            else:
                _ = self.model  # fallback: discover from a loaded model
        return self._dimension

    def embed(self, text: str | list[str]) -> np.ndarray:
        """
        Generate embeddings for text.

        Args:
            text: Single string or list of strings

        Returns:
            numpy array of embeddings (shape: [n, dimension])
        """
        if isinstance(text, str):
            text = [text]

        # Generate embeddings in batches
        embeddings = self.model.encode(
            text,
            batch_size=self.batch_size,
            convert_to_numpy=True,
            show_progress_bar=False,
            normalize_embeddings=True,  # L2 normalization for cosine similarity
        )

        return embeddings

    def embed_batch(self, texts: list[str]) -> np.ndarray:
        """
        Generate embeddings for a batch of texts.

        Alias for embed() for consistency with v0.2.0 API.

        Args:
            texts: List of strings

        Returns:
            numpy array of embeddings (shape: [n, dimension])
        """
        return self.embed(texts)

    def embed_query(self, query: str) -> np.ndarray:
        """
        Generate embedding for a search query.

        Asymmetric models (e.g. Qwen3) prepend an instruction to queries but embed
        documents bare; applying the query prompt only here — not in embed()/
        embed_batch(), which encode stored documents — keeps the two sides aligned.
        Symmetric models (query_prompt_name=None) take the same path as documents.

        Args:
            query: Search query string

        Returns:
            numpy array (shape: [dimension])
        """
        prompt_name = MODEL_REGISTRY.get(self.model_name, {}).get("query_prompt_name")
        if prompt_name:
            # Only pass prompt_name if the loaded model actually defines it; a stale
            # registry hint must degrade to a bare encode, not raise. Warn once so
            # the degradation (asymmetric model encoded symmetrically → worse
            # retrieval) is visible instead of silent.
            if prompt_name in (getattr(self.model, "prompts", None) or {}):
                embeddings = self.model.encode(
                    [query],
                    batch_size=self.batch_size,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                    normalize_embeddings=True,
                    prompt_name=prompt_name,
                )
                return embeddings[0]
            if not self._warned_missing_prompt:
                logger.warning(
                    "Model %s declares query_prompt_name=%r but the loaded model "
                    "does not define it; queries are encoded without the instruction "
                    "prefix (retrieval quality degraded).",
                    self.model_name,
                    prompt_name,
                )
                self._warned_missing_prompt = True
        return self.embed([query])[0]

    def get_info(self) -> dict:
        """
        Get embedder information.

        Returns:
            Dict with model info
        """
        model_info = MODEL_REGISTRY.get(self.model_name, {})
        return {
            "model_name": self.model_name,
            "model_path": model_info.get("name", "unknown"),
            "dimension": self.dimension,
            "device": self.device,
            "batch_size": self.batch_size,
            "size_mb": model_info.get("size_mb", 0),
            "description": model_info.get("description", ""),
        }

    def __repr__(self) -> str:
        return (
            f"Embedder(model={self.model_name}, "
            f"dimension={self.dimension if self._dimension else 'unloaded'}, "
            f"device={self.device})"
        )
