"""
Token counting utilities for mememo.

Provides accurate token counting using tiktoken for context management.
"""

import tiktoken

# Initialize tokenizer (GPT-3.5/GPT-4 compatible)
_tokenizer = None
# tiktoken.get_encoding downloads the BPE file over HTTPS on first use. With no
# disk cache and no network (the common offline/sandboxed case) that call raises
# after a ~0.8s TLS attempt. Memoize the *failure* so we don't repeat that probe
# on every count_tokens call — left un-memoized it dominated indexing time (a
# binary-search truncate × thousands of chunks = the multi-hour hang).
_tokenizer_unavailable = False


def _get_tokenizer():
    """Get the tiktoken encoder, or None if it can't be loaded (cached)."""
    global _tokenizer, _tokenizer_unavailable
    if _tokenizer is None and not _tokenizer_unavailable:
        try:
            _tokenizer = tiktoken.get_encoding("cl100k_base")  # GPT-4 encoding
        except Exception:
            _tokenizer_unavailable = True
    return _tokenizer


def count_tokens(text: str) -> int:
    """
    Count tokens in text.

    Uses the tiktoken GPT-4 encoder when it can be loaded; otherwise falls back
    to a fast offline heuristic (~4 chars/token). Counts here only gate summary
    generation and metadata, so the approximation is fine and never blocks.

    Args:
        text: Text to count tokens for

    Returns:
        Number of tokens
    """
    tokenizer = _get_tokenizer()
    if tokenizer is not None:
        try:
            return len(tokenizer.encode(text))
        except Exception:
            pass
    # ~4 characters per token is a common approximation
    return (len(text) + 3) // 4


def fits_in_budget(text: str, budget: int) -> bool:
    """
    Estimate if text fits within token budget.

    Args:
        text: Text to check
        budget: Maximum token count

    Returns:
        True if text fits within budget
    """
    return count_tokens(text) <= budget


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """
    Truncate text to fit within token budget.

    Tries to break at sentence boundaries for cleaner truncation.

    Args:
        text: Text to truncate
        max_tokens: Maximum number of tokens

    Returns:
        Truncated text
    """
    current_tokens = count_tokens(text)

    if current_tokens <= max_tokens:
        return text

    # Binary search for the right length
    low = 0
    high = len(text)
    result = ""

    while low <= high:
        mid = (low + high) // 2
        candidate = text[:mid]
        tokens = count_tokens(candidate)

        if tokens <= max_tokens:
            result = candidate
            low = mid + 1
        else:
            high = mid - 1

    # Try to break at sentence boundary
    import re

    sentences = re.split(r"[.!?]\s+", result)
    if len(sentences) > 1:
        sentences.pop()  # Remove incomplete last sentence
        return ". ".join(sentences) + "."

    return result
