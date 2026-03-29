"""
Configuration type definitions using Pydantic models.

Defines configuration structure for mememo with validation.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class StorageConfig(BaseModel):
    """Storage configuration."""

    base_dir: Path = Field(description="Base directory for all data")
    max_memory_size_mb: int = Field(default=10, gt=0, description="Max size per memory in MB")
    max_total_memories: int = Field(default=10000, gt=0, description="Max total memories")
    ttl_conversation_days: int = Field(
        default=0, ge=0, description="TTL in days for conversation memories (0 = no expiry)"
    )
    ttl_context_days: int = Field(
        default=0, ge=0, description="TTL in days for context memories (0 = no expiry)"
    )

    @field_validator("base_dir", mode="before")
    @classmethod
    def expand_path(cls, v):
        """Expand ~ and environment variables in path."""
        return Path(v).expanduser().resolve()


class EmbeddingConfig(BaseModel):
    """Embedding model configuration."""

    model_name: Literal["minilm", "gemma"] = Field(
        default="minilm", description="Embedding model: minilm (384-dim) or gemma (768-dim)"
    )
    device: Literal["auto", "cpu", "cuda", "mps"] = Field(
        default="auto", description="Device for embeddings: auto, cpu, cuda, or mps"
    )
    batch_size: int = Field(
        default=32, gt=0, le=128, description="Batch size for embedding generation"
    )


class ChunkingConfig(BaseModel):
    """Chunking configuration."""

    max_tokens: int = Field(default=500, gt=0, description="Max tokens per chunk")
    overlap_tokens: int = Field(default=50, ge=0, description="Overlap tokens between chunks")
    enable_code_aware: bool = Field(
        default=True, description="Enable code-aware chunking (AST/tree-sitter)"
    )
    preserve_structure: bool = Field(
        default=True, description="Preserve code structure (functions, classes)"
    )


class SearchConfig(BaseModel):
    """Search configuration."""

    top_k: int = Field(default=5, gt=0, le=100, description="Default number of search results")
    min_similarity: float = Field(
        default=0.7, ge=0.0, le=1.0, description="Minimum similarity threshold"
    )
    shard_size: int = Field(
        default=50000, gt=0, description="Vectors per FAISS shard (matches VectorIndex.SHARD_SIZE)"
    )


class SecurityConfig(BaseModel):
    """Security configuration."""

    enable_encryption: bool = Field(default=False, description="Enable content encryption")
    encryption_key: str | None = Field(None, description="Encryption key (never commit!)")
    enable_audit_log: bool = Field(default=False, description="Enable audit logging")
    secrets_detection: bool = Field(default=True, description="Scan for secrets before storing")
    auto_sanitize: bool = Field(default=False, description="Auto-redact secrets vs reject")

    @field_validator("encryption_key")
    @classmethod
    def validate_encryption_key(cls, v, info):
        """Validate encryption key if encryption is enabled."""
        if info.data.get("enable_encryption") and not v:
            raise ValueError("encryption_key required when enable_encryption=True")
        return v


class IndexingConfig(BaseModel):
    """Indexing configuration."""

    enable_incremental: bool = Field(default=True, description="Enable incremental indexing")
    auto_reindex_age_minutes: float = Field(
        default=5.0, gt=0, description="Auto-reindex if snapshot older than this"
    )
    ignored_dirs: list[str] = Field(
        default_factory=lambda: [
            "__pycache__",
            ".git",
            ".hg",
            ".svn",
            ".venv",
            "venv",
            "env",
            "node_modules",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "build",
            "dist",
            ".next",
            ".nuxt",
            "target",
            ".idea",
            ".vscode",
            ".coverage",
        ],
        description="Directories to ignore during indexing",
    )


class HookConfig(BaseModel):
    """Configuration for passive Claude Code hooks."""

    inject_token_budget: int = Field(
        default=800, gt=0, description="Max tokens for injected context"
    )
    inject_min_similarity: float = Field(
        default=0.25, ge=0.0, le=1.0, description="Min similarity to include in injected block"
    )
    inject_search_floor: float = Field(
        default=0.2,
        ge=0.0,
        le=1.0,
        description="Broader search floor — fetch more candidates, filter at inject_min_similarity",
    )
    capture_transcript_lines: int = Field(
        default=100, gt=0, description="Transcript tail lines to capture from"
    )
    capture_enabled: bool = Field(default=True, description="Enable Stop hook capture")
    inject_enabled: bool = Field(default=True, description="Enable UserPromptSubmit injection")

    # Smart context selection
    smart_context_enabled: bool = Field(
        default=True, description="Enable intent-aware adaptive context selection"
    )
    intent_confidence_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Minimum confidence for intent classification (below this, falls back to 'general')",
    )
    inject_token_budget_min: int = Field(
        default=200, gt=0, description="Minimum dynamic token budget for injection"
    )
    inject_token_budget_max: int = Field(
        default=1200, gt=0, description="Maximum dynamic token budget for injection"
    )
    skill_injection_enabled: bool = Field(
        default=True, description="Enable skill prompt injection based on intent"
    )
    skill_token_budget: int = Field(
        default=200, gt=0, description="Max tokens reserved for skill prompts"
    )
    response_compression_enabled: bool = Field(
        default=True, description="Enable transcript compression before capture"
    )
    capture_dedup_similarity: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Similarity threshold for dedup during capture (skip already-stored facts)",
    )


class Config(BaseModel):
    """Complete mememo configuration."""

    storage: StorageConfig
    embedding: EmbeddingConfig
    chunking: ChunkingConfig
    search: SearchConfig
    security: SecurityConfig
    indexing: IndexingConfig
    hook: HookConfig = Field(default_factory=HookConfig)

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        import os
        from pathlib import Path

        return cls(
            storage=StorageConfig(
                base_dir=Path(
                    os.getenv(
                        "MEMEMO_STORAGE_DIR",
                        os.getenv("MEMEMO_DATA_DIR", "~/.mememo/data"),  # Backward compatibility
                    )
                ),
                max_memory_size_mb=int(os.getenv("MEMEMO_MAX_MEMORY_SIZE_MB", "10")),
                max_total_memories=int(os.getenv("MEMEMO_MAX_TOTAL_MEMORIES", "10000")),
                ttl_conversation_days=int(os.getenv("MEMEMO_TTL_CONVERSATION_DAYS", "0")),
                ttl_context_days=int(os.getenv("MEMEMO_TTL_CONTEXT_DAYS", "0")),
            ),
            embedding=EmbeddingConfig(
                model_name=os.getenv("MEMEMO_EMBEDDING_MODEL", "minilm"),
                device=os.getenv("MEMEMO_EMBEDDING_DEVICE", "auto"),
                batch_size=int(os.getenv("MEMEMO_EMBEDDING_BATCH_SIZE", "32")),
            ),
            chunking=ChunkingConfig(
                max_tokens=int(os.getenv("MEMEMO_CHUNK_MAX_TOKENS", "500")),
                overlap_tokens=int(os.getenv("MEMEMO_CHUNK_OVERLAP_TOKENS", "50")),
                enable_code_aware=os.getenv("MEMEMO_ENABLE_CODE_AWARE", "true").lower() == "true",
            ),
            search=SearchConfig(
                top_k=int(os.getenv("MEMEMO_SEARCH_TOP_K", "5")),
                min_similarity=float(os.getenv("MEMEMO_SEARCH_MIN_SIMILARITY", "0.7")),
            ),
            security=SecurityConfig(
                enable_encryption=os.getenv("MEMEMO_ENABLE_ENCRYPTION", "false").lower() == "true",
                encryption_key=os.getenv("MEMEMO_ENCRYPTION_KEY"),
                enable_audit_log=os.getenv("MEMEMO_ENABLE_AUDIT_LOG", "false").lower() == "true",
                secrets_detection=os.getenv("MEMEMO_SECRETS_DETECTION", "true").lower() == "true",
                auto_sanitize=os.getenv("MEMEMO_AUTO_SANITIZE", "false").lower() == "true",
            ),
            indexing=IndexingConfig(
                enable_incremental=os.getenv("MEMEMO_ENABLE_INCREMENTAL", "true").lower() == "true",
                auto_reindex_age_minutes=float(os.getenv("MEMEMO_AUTO_REINDEX_AGE_MINUTES", "5.0")),
            ),
            hook=HookConfig(
                inject_token_budget=int(os.getenv("MEMEMO_HOOK_INJECT_TOKEN_BUDGET", "800")),
                inject_min_similarity=float(os.getenv("MEMEMO_HOOK_INJECT_MIN_SIMILARITY", "0.25")),
                inject_search_floor=float(os.getenv("MEMEMO_HOOK_INJECT_SEARCH_FLOOR", "0.2")),
                capture_transcript_lines=int(os.getenv("MEMEMO_HOOK_CAPTURE_LINES", "100")),
                capture_enabled=os.getenv("MEMEMO_HOOK_CAPTURE_ENABLED", "true").lower() == "true",
                inject_enabled=os.getenv("MEMEMO_HOOK_INJECT_ENABLED", "true").lower() == "true",
                smart_context_enabled=os.getenv("MEMEMO_SMART_CONTEXT_ENABLED", "true").lower()
                == "true",
                intent_confidence_threshold=float(
                    os.getenv("MEMEMO_INTENT_CONFIDENCE_THRESHOLD", "0.3")
                ),
                inject_token_budget_min=int(
                    os.getenv("MEMEMO_HOOK_INJECT_TOKEN_BUDGET_MIN", "200")
                ),
                inject_token_budget_max=int(
                    os.getenv("MEMEMO_HOOK_INJECT_TOKEN_BUDGET_MAX", "1200")
                ),
                skill_injection_enabled=os.getenv("MEMEMO_SKILL_INJECTION_ENABLED", "true").lower()
                == "true",
                skill_token_budget=int(os.getenv("MEMEMO_SKILL_TOKEN_BUDGET", "200")),
                response_compression_enabled=os.getenv(
                    "MEMEMO_RESPONSE_COMPRESSION_ENABLED", "true"
                ).lower()
                == "true",
                capture_dedup_similarity=float(
                    os.getenv("MEMEMO_CAPTURE_DEDUP_SIMILARITY", "0.85")
                ),
            ),
        )


# Alias for backward compatibility
MemoConfig = Config
