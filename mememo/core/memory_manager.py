"""
Memory Manager for mememo.

Orchestrates all memory operations:
- Git context detection
- Content validation (secrets detection)
- Embedding generation
- Vector indexing
- Metadata storage
"""

import logging
import re
from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from ..types import (
    Memory,
    MemoryContent,
    MemoryMetadata,
    MemoryRelationships,
    MemorySummary,
    CreateMemoryParams,
    MemoryFilters,
    SearchParams,
    SearchResult,
    GitContext,
)
from ..utils import calculate_checksum, count_tokens, truncate_to_tokens, SecretsDetector
from .git_manager import GitManager
from .storage_manager import StorageManager
from .vector_index import VectorIndex
from ..embeddings import Embedder

logger = logging.getLogger(__name__)


class MemoryManager:
    """
    MemoryManager orchestrates all memory operations.

    Coordinates:
    - Git context detection (branch isolation)
    - Content validation (secrets detection)
    - Embedding generation
    - Vector indexing (FAISS)
    - Metadata storage (SQLite + JSON)
    """

    def __init__(
        self,
        git_manager: GitManager,
        storage_manager: StorageManager,
        embedder: Embedder,
        vector_index: VectorIndex,
        auto_sanitize: bool = False,
        secrets_detection: bool = True,
    ):
        """
        Initialize memory manager.

        Args:
            git_manager: Git context manager
            storage_manager: Storage manager
            embedder: Embedding generator
            vector_index: Vector index
            auto_sanitize: Auto-sanitize secrets (vs reject)
            secrets_detection: Enable secrets detection
        """
        self.git_manager = git_manager
        self.storage_manager = storage_manager
        self.embedder = embedder
        self.vector_index = vector_index
        self.auto_sanitize = auto_sanitize
        self.secrets_detection = secrets_detection

        if secrets_detection:
            self.secrets_detector = SecretsDetector()
        else:
            self.secrets_detector = None

        logger.info("MemoryManager initialized")

    async def create_memory(self, params: CreateMemoryParams) -> Memory:
        """
        Create a new memory.

        Full workflow:
        1. Validate content (check for secrets)
        2. Detect git context (repo + branch)
        3. Generate embedding
        4. Store metadata and content
        5. Add to vector index

        Args:
            params: Memory creation parameters

        Returns:
            Created memory object

        Raises:
            ValueError: If secrets detected and auto_sanitize=False
        """
        # 1. Validate content (check for secrets)
        validated_content = self._validate_content(params.content)

        # 2. Detect git context (repo + branch)
        context = await self.git_manager.detect_context()

        # 3. Generate UUID for memory ID
        memory_id = str(uuid4())

        # 4. Calculate checksum for deduplication
        checksum = calculate_checksum(validated_content)

        # 5. Count tokens for efficiency tracking
        token_count = count_tokens(validated_content)

        # 6. Generate summaries
        one_line = self._generate_one_line(validated_content)
        detailed_summary = (
            self._generate_detailed_summary(validated_content)
            if token_count > 200
            else None
        )

        # 7. Generate embedding
        logger.debug(f"Generating embedding for memory {memory_id}")
        embedding = self.embedder.embed_query(validated_content)

        # 8. Create memory object
        now = datetime.now()
        memory = Memory(
            id=memory_id,
            repo=context.repo,
            branch=context.branch,
            content=MemoryContent(
                type=params.type,
                text=validated_content,
                language=params.language,
                file_path=params.file_path,
                line_range=params.line_range,
                # NEW in v0.3.0: Code-aware metadata
                function_name=params.function_name,
                class_name=params.class_name,
                docstring=params.docstring,
                decorators=params.decorators,
                parent_class=params.parent_class,
            ),
            metadata=MemoryMetadata(
                tags=params.tags or [],
                created_at=now,
                updated_at=now,
                checksum=checksum,
                token_count=token_count,
            ),
            relationships=params.relationships or MemoryRelationships(),
            summary=MemorySummary(
                one_line=one_line,
                detailed=detailed_summary,
            ),
        )

        # 9. Save to storage
        logger.debug(f"Saving memory {memory_id} to storage")
        await self.storage_manager.save_memory(memory)

        # 10. Add to vector index
        logger.debug(f"Adding memory {memory_id} to vector index")
        self.vector_index.add(
            embeddings=[embedding.tolist()],
            memory_ids=[memory_id],
            checksums=[checksum],
        )

        logger.info(f"Created memory {memory_id} ({token_count} tokens)")
        return memory

    async def retrieve_memory(self, memory_id: str) -> Memory:
        """
        Retrieve memory by ID.

        Args:
            memory_id: Memory ID

        Returns:
            Memory object
        """
        context = await self.git_manager.detect_context()
        return await self.storage_manager.load_memory(memory_id, context)

    async def find_memories(self, filters: MemoryFilters) -> List[Memory]:
        """
        Find memories with filters.

        Args:
            filters: Query filters

        Returns:
            List of matching memories
        """
        context = await self.git_manager.detect_context()
        return await self.storage_manager.find_memories(filters, context)

    async def search_similar(self, params: SearchParams) -> List[SearchResult]:
        """
        Search for similar memories using vector similarity.

        Args:
            params: Search parameters

        Returns:
            List of search results with similarity scores
        """
        context = await self.git_manager.detect_context()

        # Generate embedding for query
        logger.debug(f"Generating embedding for query: {params.query[:50]}...")
        query_embedding = self.embedder.embed_query(params.query)

        # Search vector index
        top_k = params.top_k
        distances, memory_ids = self.vector_index.search(
            query_embedding=query_embedding.tolist(),
            top_k=top_k,
        )

        # Convert L2 distances to similarity scores (0-1 range)
        # Lower distance = higher similarity
        # Using exponential decay: similarity = exp(-distance)
        import math

        results: List[SearchResult] = []

        for memory_id, distance in zip(memory_ids, distances):
            # Convert L2 distance to similarity score
            similarity = math.exp(-distance)

            # Apply minimum similarity threshold
            if similarity < params.min_similarity:
                continue

            # Load memory
            try:
                memory = await self.storage_manager.load_memory(memory_id, context)

                # Apply type filter if specified
                if params.type and memory.content.type != params.type:
                    continue

                results.append(
                    SearchResult(
                        memory=memory,
                        similarity=similarity,
                    )
                )
            except ValueError:
                # Memory not found - index out of sync, skip
                logger.warning(f"Memory {memory_id} not found in storage")
                continue

        logger.info(f"Found {len(results)} similar memories")
        return results

    async def delete_memory(self, memory_id: str) -> None:
        """
        Delete memory.

        Args:
            memory_id: Memory ID to delete
        """
        context = await self.git_manager.detect_context()
        await self.storage_manager.delete_memory(memory_id, context)

        # Delete from vector index
        self.vector_index.delete_by_memory_id(memory_id)

        logger.info(f"Deleted memory {memory_id}")

    def get_statistics(self) -> dict:
        """
        Get storage and index statistics.

        Returns:
            Dict with statistics
        """
        storage_stats = self.storage_manager.get_statistics()
        vector_stats = self.vector_index.get_statistics()

        return {
            **storage_stats,
            "vector_index": vector_stats,
            "embedder": self.embedder.get_info(),
        }

    async def summarize_memories(
        self,
        memory_ids: List[str],
        max_tokens: int = 500,
    ) -> str:
        """
        Summarize multiple memories into a hierarchical summary.

        Args:
            memory_ids: List of memory IDs to summarize
            max_tokens: Maximum tokens in summary

        Returns:
            Hierarchical summary text
        """
        context = await self.git_manager.detect_context()

        # Load all memories
        memories = []
        for memory_id in memory_ids:
            try:
                memory = await self.storage_manager.load_memory(memory_id, context)
                memories.append(memory)
            except ValueError:
                logger.warning(f"Memory {memory_id} not found, skipping")
                continue

        # Group by file
        by_file: dict[str, List[Memory]] = {}
        for memory in memories:
            file_path = memory.content.file_path or "unknown"
            if file_path not in by_file:
                by_file[file_path] = []
            by_file[file_path].append(memory)

        # Build hierarchical summary
        summary_parts = []
        current_tokens = 0

        for file_path, file_memories in by_file.items():
            # File header
            file_header = f"\n## {file_path}\n"
            current_tokens += count_tokens(file_header)

            if current_tokens > max_tokens:
                break
            summary_parts.append(file_header)

            # Add one-line summaries for each memory
            for memory in file_memories:
                line = f"- {memory.summary.one_line}\n"
                current_tokens += count_tokens(line)

                if current_tokens > max_tokens:
                    break
                summary_parts.append(line)

        return "".join(summary_parts)

    def _validate_content(self, content: str) -> str:
        """
        Validate content for secrets.

        Args:
            content: Content to validate

        Returns:
            Validated (and possibly sanitized) content

        Raises:
            ValueError: If secrets detected and auto_sanitize=False
        """
        if not self.secrets_detection or not self.secrets_detector:
            return content

        if self.secrets_detector.has_secrets(content):
            if self.auto_sanitize:
                logger.warning("Secrets detected - auto-sanitizing")
                return self.secrets_detector.sanitize(content)
            else:
                report = self.secrets_detector.get_report(content)
                raise ValueError(f"Secrets detected in content:\n{report}")

        return content

    def _generate_one_line(self, content: str) -> str:
        """
        Generate one-line summary.

        Uses first sentence or first 100 characters.

        Args:
            content: Content to summarize

        Returns:
            One-line summary
        """
        # Try to extract first sentence
        match = re.match(r"^[^.!?]+[.!?]", content)
        if match:
            sentence = match.group(0).strip()
            if len(sentence) <= 100:
                return sentence

        # Fallback: first 100 chars with ellipsis
        if len(content) <= 100:
            return content.strip()

        return content[:97].strip() + "..."

    def _generate_detailed_summary(self, content: str) -> str:
        """
        Generate detailed summary.

        Uses extractive summarization (first paragraph or key sentences).

        Args:
            content: Content to summarize

        Returns:
            Detailed summary
        """
        # Extract first paragraph (up to double newline)
        first_paragraph = content.split("\n\n")[0]

        # Truncate to 500 tokens if needed
        return truncate_to_tokens(first_paragraph, 500)
