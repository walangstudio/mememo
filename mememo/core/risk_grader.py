"""Risk grading for commit-aware staleness (FR-007 / FR-008 / FR-009).

Pure functions — no SQLite, no git subprocess. Inputs:
- diff map: {file_path: change_kind} from GitManager.diff_between
- memory metadata: file_path + optional line_range (start, end)

Output: one of WILL_BREAK / LIKELY_AFFECTED / MAY_NEED_TESTING, or None
when the memory's file is untouched by the diff.

The grading rules are intentionally conservative; the SHOULD-clause
line-overlap downgrade (FR-009) is implemented when the diff carries
hunk ranges. When only file-level change_kind is available the grader
falls back to LIKELY_AFFECTED for memories with a line_range and
MAY_NEED_TESTING when they don't (a memory with no line_range is most
likely a doc-level chunk that won't crash the caller).
"""

from __future__ import annotations

from typing import Iterable

from ..types.memory import RiskGrade

# Git --name-status change kinds. We treat R (rename) on the old path as
# equivalent to D (deletion) and ignore it on the new path (the memory
# doesn't yet exist for the new location).
DELETE_LIKE = frozenset({"D"})
MODIFY_LIKE = frozenset({"M", "T", "C"})  # modified, type-change, copied-content
RENAME = "R"


def _lines_overlap(
    memory_range: tuple[int, int] | None,
    changed_ranges: Iterable[tuple[int, int]] | None,
) -> bool:
    """True if the memory's line_range intersects any hunk range in the diff."""
    if memory_range is None or not changed_ranges:
        return False
    m_start, m_end = memory_range
    for c_start, c_end in changed_ranges:
        if c_end >= m_start and c_start <= m_end:
            return True
    return False


def grade_memory(
    memory_file: str | None,
    memory_line_range: tuple[int, int] | None,
    diff: dict[str, str],
    hunk_ranges: dict[str, list[tuple[int, int]]] | None = None,
) -> RiskGrade | None:
    """Grade a single memory against a file-level (or hunk-level) diff.

    Args:
        memory_file: memory.content.file_path (None means the memory has no
            file binding — never affected).
        memory_line_range: (start, end) line numbers from the chunk, or None.
        diff: result of GitManager.diff_between (path -> change_kind).
        hunk_ranges: optional path -> list of (start, end) line ranges that
            actually changed. Enables FR-009 SHOULD-clause downgrade.

    Returns:
        RiskGrade or None when the memory is not in the diff.
    """
    if not memory_file:
        return None
    kind = diff.get(memory_file)
    if kind is None:
        return None

    if kind in DELETE_LIKE or kind == RENAME:
        return "WILL_BREAK"

    if kind in MODIFY_LIKE:
        # FR-009 SHOULD: downgrade when memory's lines are untouched.
        if hunk_ranges is not None and memory_line_range is not None:
            if _lines_overlap(memory_line_range, hunk_ranges.get(memory_file)):
                return "LIKELY_AFFECTED"
            return "MAY_NEED_TESTING"
        # File-level only: be conservative.
        return "LIKELY_AFFECTED" if memory_line_range is not None else "MAY_NEED_TESTING"

    # Added files (A) can't affect existing memories; everything else maps
    # to "may need testing" as a safe default.
    return "MAY_NEED_TESTING"
