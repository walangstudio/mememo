"""
Text-based chunker (fallback).

Simple token-based chunking for unsupported languages or when parsing fails.
"""

import logging
import re
from typing import List

from .base_chunker import BaseChunker, Chunk
from ..utils import count_tokens

logger = logging.getLogger(__name__)


class TextChunker(BaseChunker):
    """
    Text-based chunker using token boundaries.

    Fallback chunker when:
    - Language not supported
    - Parsing fails
    - Non-code files
    """

    def __init__(self, max_tokens: int = 500, overlap_tokens: int = 50):
        """
        Initialize text chunker.

        Args:
            max_tokens: Maximum tokens per chunk
            overlap_tokens: Overlap between chunks
        """
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(self, code: str, file_path: str) -> List[Chunk]:
        """
        Chunk text using sentence and token boundaries.

        Args:
            code: Text content
            file_path: Path to file

        Returns:
            List of text chunks
        """
        # Split into sentences
        sentences = re.split(r'([.!?]\s+)', code)

        # Rebuild sentences with punctuation
        full_sentences = []
        for i in range(0, len(sentences) - 1, 2):
            if i + 1 < len(sentences):
                full_sentences.append(sentences[i] + sentences[i + 1])
            else:
                full_sentences.append(sentences[i])

        if not full_sentences:
            full_sentences = [code]

        chunks = []
        current_chunk = []
        current_tokens = 0
        start_line = 1
        current_line = 1

        for sentence in full_sentences:
            sentence_tokens = count_tokens(sentence)

            # If single sentence exceeds max, split by newlines
            if sentence_tokens > self.max_tokens:
                if current_chunk:
                    # Save current chunk
                    chunk_text = "".join(current_chunk)
                    chunks.append(
                        Chunk(
                            text=chunk_text,
                            start_line=start_line,
                            end_line=current_line,
                            chunk_type="text",
                            file_path=file_path,
                        )
                    )
                    current_chunk = []
                    current_tokens = 0
                    start_line = current_line + 1

                # Split long sentence by lines
                lines = sentence.split("\n")
                temp_chunk = []
                temp_tokens = 0

                for line in lines:
                    line_tokens = count_tokens(line)
                    if temp_tokens + line_tokens > self.max_tokens and temp_chunk:
                        # Save temp chunk
                        chunks.append(
                            Chunk(
                                text="\n".join(temp_chunk),
                                start_line=start_line,
                                end_line=current_line,
                                chunk_type="text",
                                file_path=file_path,
                            )
                        )
                        temp_chunk = []
                        temp_tokens = 0
                        start_line = current_line + 1

                    temp_chunk.append(line)
                    temp_tokens += line_tokens
                    current_line += 1

                if temp_chunk:
                    chunks.append(
                        Chunk(
                            text="\n".join(temp_chunk),
                            start_line=start_line,
                            end_line=current_line,
                            chunk_type="text",
                            file_path=file_path,
                        )
                    )
                    start_line = current_line + 1

                continue

            # Check if adding sentence exceeds max
            if current_tokens + sentence_tokens > self.max_tokens and current_chunk:
                # Save current chunk
                chunk_text = "".join(current_chunk)
                chunks.append(
                    Chunk(
                        text=chunk_text,
                        start_line=start_line,
                        end_line=current_line,
                        chunk_type="text",
                        file_path=file_path,
                    )
                )

                # Start new chunk with overlap
                if self.overlap_tokens > 0 and current_chunk:
                    overlap_text = "".join(current_chunk[-2:])  # Last 2 sentences
                    overlap_tok = count_tokens(overlap_text)
                    if overlap_tok <= self.overlap_tokens:
                        current_chunk = current_chunk[-2:]
                        current_tokens = overlap_tok
                    else:
                        current_chunk = []
                        current_tokens = 0
                else:
                    current_chunk = []
                    current_tokens = 0

                start_line = current_line

            # Add sentence to current chunk
            current_chunk.append(sentence)
            current_tokens += sentence_tokens
            current_line += sentence.count("\n")

        # Save final chunk
        if current_chunk:
            chunk_text = "".join(current_chunk)
            chunks.append(
                Chunk(
                    text=chunk_text,
                    start_line=start_line,
                    end_line=current_line,
                    chunk_type="text",
                    file_path=file_path,
                )
            )

        logger.debug(f"Text chunker created {len(chunks)} chunks from {file_path}")
        return chunks if chunks else [
            Chunk(
                text=code,
                start_line=1,
                end_line=code.count("\n") + 1,
                chunk_type="text",
                file_path=file_path,
            )
        ]
