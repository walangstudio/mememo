"""Tests for adaptive context builder."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from mememo.context.adaptive_builder import AdaptiveContextBuilder, BuilderConfig, BuildResult


def _make_result(similarity, mem_type, text, created_days_ago=0, file_path=None):
    """Create a mock search result."""
    r = MagicMock()
    r.similarity = similarity
    r.memory.content.type = mem_type
    r.memory.content.text = text
    r.memory.content.file_path = file_path
    r.memory.summary.one_line = text[:50]
    r.memory.summary.detailed = text[:100] if len(text) > 200 else None
    r.memory.metadata.created_at = datetime.now() - timedelta(days=created_days_ago)
    return r


def test_empty_results():
    builder = AdaptiveContextBuilder("general", 0.5, BuilderConfig())
    result = builder.build([])
    assert result.block is None
    assert result.entries_included == 0


def test_all_below_similarity_threshold():
    cfg = BuilderConfig(min_similarity=0.5)
    builder = AdaptiveContextBuilder("general", 0.5, cfg)
    results = [_make_result(0.1, "context", "low similarity")]
    result = builder.build(results)
    assert result.block is None


def test_tier1_full_text():
    cfg = BuilderConfig(base_budget=500, min_budget=100, max_budget=1000)
    builder = AdaptiveContextBuilder("debugging", 0.8, cfg)
    results = [_make_result(0.9, "analysis", "Important bug analysis about memory leak")]
    result = builder.build(results)
    assert result.block is not None
    assert "Important bug analysis" in result.block
    assert result.entries_included == 1


def test_dynamic_budget_high_quality():
    cfg = BuilderConfig(base_budget=800, min_budget=200, max_budget=1200)
    builder = AdaptiveContextBuilder("coding", 0.9, cfg)
    # High similarity = high composite = budget expands
    results = [_make_result(0.95, "code_snippet", "def foo(): pass")]
    result = builder.build(results)
    assert result.effective_budget > 800


def test_dynamic_budget_low_quality():
    cfg = BuilderConfig(base_budget=800, min_budget=200, max_budget=1200)
    builder = AdaptiveContextBuilder("general", 0.3, cfg)
    # Low similarity + low type priority = low composite = budget shrinks
    results = [_make_result(0.3, "relationship", "vaguely related")]
    result = builder.build(results)
    assert result.effective_budget < 800


def test_budget_respects_min_max():
    cfg = BuilderConfig(base_budget=800, min_budget=300, max_budget=1000)
    builder = AdaptiveContextBuilder("general", 0.1, cfg)
    results = [_make_result(0.25, "context", "very low")]
    result = builder.build(results)
    assert result.effective_budget >= 300


def test_skill_tokens_deducted():
    cfg = BuilderConfig(base_budget=800, min_budget=200, max_budget=1200)
    builder = AdaptiveContextBuilder("coding", 0.8, cfg)
    results = [_make_result(0.9, "code_snippet", "def bar(): return 42")]
    result = builder.build(results, skill_tokens_used=500)
    # Effective budget should be reduced by skill tokens
    assert result.effective_budget < builder._compute_effective_budget(
        builder._composite_score(0.9, "code_snippet", datetime.now().timestamp())
    )


def test_type_priority_affects_ordering():
    cfg = BuilderConfig(base_budget=1000, min_budget=200, max_budget=1200, min_similarity=0.2)
    builder = AdaptiveContextBuilder("debugging", 0.8, cfg)
    # analysis has higher priority for debugging than context
    results = [
        _make_result(0.5, "context", "some context info"),
        _make_result(0.5, "analysis", "bug analysis details"),
    ]
    result = builder.build(results)
    assert result.block is not None
    # Analysis should appear first due to higher type priority
    lines = result.block.split("\n")
    assert "analysis" in lines[0]


def test_recency_affects_score():
    cfg = BuilderConfig(base_budget=1000, min_budget=200, max_budget=1200, min_similarity=0.2)
    builder = AdaptiveContextBuilder("general", 0.5, cfg)
    results = [
        _make_result(0.5, "context", "old context", created_days_ago=100),
        _make_result(0.5, "context", "recent context", created_days_ago=0),
    ]
    result = builder.build(results)
    assert result.block is not None
    lines = result.block.split("\n")
    assert "recent" in lines[0]


def test_build_result_metadata():
    cfg = BuilderConfig(base_budget=800, min_budget=200, max_budget=1200)
    builder = AdaptiveContextBuilder("testing", 0.7, cfg)
    results = [_make_result(0.8, "code_snippet", "def test_foo(): assert True")]
    result = builder.build(results)
    assert result.intent == "testing"
    assert result.intent_confidence == 0.7
    assert result.tokens_used > 0
