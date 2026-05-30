"""Shared URL extraction utilities for chunkers.

scan_urls  — extract URLs from raw text, strip trailing punctuation, dedup.
normalize_url — lowercase scheme+host, strip trailing slash (for dedup keys).

The same regex and trailing-punct semantics as markdown_chunker._scan_urls,
but operating on raw text (not a list of body lines, and no fence-skip
because plain docstrings don't have Markdown fences).
"""

from __future__ import annotations

import re

# Scheme-relative URL pattern — matches http/https and any other URI scheme.
_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)

# Trailing punctuation that is sentence-grammar, not part of the URL.
_TRAILING_PUNCT = ".,;:!?)]>}\"'`"


def scan_urls(text: str) -> list[str]:
    """Return unique URLs found in *text*, in order of first occurrence.

    Trailing sentence punctuation (period, comma, semicolon, colon, bang,
    question mark, closing brackets, quotes, backtick) is stripped from each
    match.  Duplicates (after stripping) are removed; only the first occurrence
    is kept.
    """
    seen: set[str] = set()
    result: list[str] = []
    for raw in _URL_RE.findall(text):
        url = raw.rstrip(_TRAILING_PUNCT)
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def normalize_url(url: str) -> str:
    """Return a canonical form of *url* for deduplication.

    Lowercases scheme and host; strips a single trailing slash on the path.
    Does NOT decode percent-encoding or normalise query strings — keeps it
    lightweight and dependency-free.
    """
    try:
        # Split on '://' to isolate scheme, then split host from path.
        scheme_rest = url.split("://", 1)
        if len(scheme_rest) != 2:
            return url.lower().rstrip("/")
        scheme, rest = scheme_rest
        # rest = host/path?query#fragment  (or host alone)
        slash_idx = rest.find("/")
        if slash_idx == -1:
            return f"{scheme.lower()}://{rest.lower()}"
        host = rest[:slash_idx].lower()
        path = rest[slash_idx:].rstrip("/")
        return f"{scheme.lower()}://{host}{path}"
    except Exception:
        return url
