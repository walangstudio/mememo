"""
Token counting utilities for mememo.

Provides accurate token counting using tiktoken for context management.
"""

import tiktoken


# Initialize tokenizer (GPT-3.5/GPT-4 compatible)
_tokenizer = None


def _get_tokenizer():
    """Get or initialize the tokenizer."""
    global _tokenizer
    if _tokenizer is None:
        _tokenizer = tiktoken.get_encoding("cl100k_base")  # GPT-4 encoding
    return _tokenizer


def count_tokens(text: str) -> int:
    """
    Count tokens in text using GPT tokenizer.

    Provides accurate token counts for context management.

    Args:
        text: Text to count tokens for

    Returns:
        Number of tokens
    """
    try:
        tokenizer = _get_tokenizer()
        tokens = tokenizer.encode(text)
        return len(tokens)
    except Exception:
        # Fallback to rough estimation if encoding fails
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
    sentences = re.split(r'[.!?]\s+', result)
    if len(sentences) > 1:
        sentences.pop()  # Remove incomplete last sentence
        return '. '.join(sentences) + '.'

    return result
