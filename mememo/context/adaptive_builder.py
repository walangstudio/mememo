"""
Adaptive context builder with intent-aware, budget-dynamic assembly.

Replaces the fixed _build_context_block with graduated compression
tiers and dynamic budget sizing based on result quality.
"""

import logging
import math
import time
from dataclasses import dataclass

from ..utils.token_counter import count_tokens
from .intent_classifier import IntentClassifier

logger = logging.getLogger(__name__)


@dataclass
class BuilderConfig:
    base_budget: int = 800
    min_budget: int = 200
    max_budget: int = 1200
    min_similarity: float = 0.25


@dataclass
class BuildResult:
    block: str | None
    effective_budget: int
    intent: str
    intent_confidence: float
    entries_included: int
    tokens_used: int


class AdaptiveContextBuilder:
    def __init__(self, intent: str, intent_confidence: float, config: BuilderConfig):
        self._intent = intent
        self._confidence = intent_confidence
        self._config = config

    def _compute_effective_budget(self, best_composite: float) -> int:
        quality_multiplier = 0.5 + best_composite
        raw = int(self._config.base_budget * quality_multiplier)
        return max(self._config.min_budget, min(self._config.max_budget, raw))

    @staticmethod
    def _recency_score(created_at_epoch: float) -> float:
        age_days = (time.time() - created_at_epoch) / 86400
        return max(0.3, math.exp(-age_days / 60))

    def _composite_score(self, similarity: float, memory_type: str, created_at_epoch: float) -> float:
        type_priority = IntentClassifier.get_type_priority(self._intent, memory_type)
        recency = self._recency_score(created_at_epoch)
        return similarity * 0.6 + type_priority * 0.3 + recency * 0.1

    @staticmethod
    def _format_tier1(memory) -> str:
        text = memory.content.text.strip()
        token_count = count_tokens(text)
        if token_count > 300 and memory.summary.detailed:
            text = memory.summary.detailed
        elif token_count > 300:
            text = memory.summary.one_line
        return f"- [{memory.content.type}] {text}"

    @staticmethod
    def _format_tier2(memory) -> str:
        loc = memory.content.file_path or ""
        summary = memory.summary.one_line
        if loc:
            return f"- [{memory.content.type}] {summary} -- {loc}"
        return f"- [{memory.content.type}] {summary}"

    @staticmethod
    def _format_tier3(memory) -> str:
        return f"- [{memory.content.type}] {memory.summary.one_line}"

    def build(self, results, skill_tokens_used: int = 0) -> BuildResult:
        if not results:
            return BuildResult(
                block=None,
                effective_budget=self._config.min_budget,
                intent=self._intent,
                intent_confidence=self._confidence,
                entries_included=0,
                tokens_used=0,
            )

        scored = []
        for r in results:
            if r.similarity < self._config.min_similarity:
                continue
            created_epoch = r.memory.metadata.created_at.timestamp()
            composite = self._composite_score(r.similarity, r.memory.content.type, created_epoch)
            scored.append((composite, r))

        scored.sort(key=lambda x: x[0], reverse=True)

        if not scored:
            return BuildResult(
                block=None,
                effective_budget=self._config.min_budget,
                intent=self._intent,
                intent_confidence=self._confidence,
                entries_included=0,
                tokens_used=0,
            )

        best_composite = scored[0][0]
        effective_budget = self._compute_effective_budget(best_composite) - skill_tokens_used

        if effective_budget <= 0:
            return BuildResult(
                block=None,
                effective_budget=0,
                intent=self._intent,
                intent_confidence=self._confidence,
                entries_included=0,
                tokens_used=0,
            )

        lines: list[str] = []
        used_tokens = 0

        for composite, r in scored:
            mem = r.memory

            # Determine tier and format
            if composite >= 0.7:
                entry = self._format_tier1(mem)
            elif composite >= 0.5:
                entry = self._format_tier2(mem)
            elif composite >= 0.3:
                entry = self._format_tier3(mem)
            else:
                continue

            entry_tokens = count_tokens(entry)

            # Downgrade tier if entry doesn't fit in budget
            if used_tokens + entry_tokens > effective_budget:
                if composite >= 0.5:
                    entry = self._format_tier2(mem)
                    entry_tokens = count_tokens(entry)
                if used_tokens + entry_tokens > effective_budget:
                    entry = self._format_tier3(mem)
                    entry_tokens = count_tokens(entry)
                if used_tokens + entry_tokens > effective_budget:
                    continue

            lines.append(entry)
            used_tokens += entry_tokens

        if not lines:
            return BuildResult(
                block=None,
                effective_budget=effective_budget,
                intent=self._intent,
                intent_confidence=self._confidence,
                entries_included=0,
                tokens_used=0,
            )

        return BuildResult(
            block="\n".join(lines),
            effective_budget=effective_budget,
            intent=self._intent,
            intent_confidence=self._confidence,
            entries_included=len(lines),
            tokens_used=used_tokens,
        )
