"""
Response compressor for capture-side token conservation.

Preprocesses transcript text to strip boilerplate before LLM extraction,
and augments the capture prompt with already-stored memory summaries to
prevent redundant extraction.
"""

import re

_TOOL_BLOCK_RE = re.compile(
    r"(assistant:\s*)?<tool_use>.*?</tool_use>|<tool_result>.*?</tool_result>",
    re.DOTALL,
)
_CODE_BLOCK_RE = re.compile(r"```[\w]*\n(.*?)```", re.DOTALL)
_MEMEMO_INJECT_RE = re.compile(
    r"Relevant memories from previous sessions:\n(?:- \[.*\].*\n?)+",
)
_SYSTEM_REMINDER_RE = re.compile(
    r"<system-reminder>.*?</system-reminder>",
    re.DOTALL,
)
_PROGRESS_RE = re.compile(r"[▓░█▌▐]{3,}|\.{6,}|[=]{5,}>?")
_WHITESPACE_RE = re.compile(r"\n{3,}")


class ResponseCompressor:
    def preprocess(self, text: str) -> str:
        if not text:
            return text

        # Strip tool call/result blocks (keep tool name if detectable)
        result = _TOOL_BLOCK_RE.sub("", text)

        # Strip mememo's own injected context and system-reminder blocks
        result = _MEMEMO_INJECT_RE.sub("", result)
        result = _SYSTEM_REMINDER_RE.sub("", result)

        # Truncate long code blocks (>30 lines) to first/last 5 lines
        result = self._truncate_code_blocks(result)

        # Strip progress bars, loading indicators
        result = _PROGRESS_RE.sub("", result)

        # Collapse excessive whitespace
        result = _WHITESPACE_RE.sub("\n\n", result)

        return result.strip()

    def _truncate_code_blocks(self, text: str) -> str:
        def _truncate_match(match):
            code = match.group(1)
            lines = code.splitlines()
            if len(lines) <= 30:
                return match.group(0)
            head = "\n".join(lines[:5])
            tail = "\n".join(lines[-5:])
            omitted = len(lines) - 10
            return f"```\n{head}\n... {omitted} lines omitted ...\n{tail}\n```"

        return _CODE_BLOCK_RE.sub(_truncate_match, text)

    @staticmethod
    def build_enhanced_prompt(base_prompt: str, existing_summaries: list[str]) -> str:
        if not existing_summaries:
            return base_prompt

        already_known = "\n".join(f"- {s}" for s in existing_summaries[:20])
        return f"{base_prompt}\n\n" f"Already stored (do NOT re-extract these):\n{already_known}"
