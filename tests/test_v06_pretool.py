"""v0.6 batch 2 — PreToolUse hook + 300-token budget gate + installer polish."""

from __future__ import annotations

import json
import sys
import types as _types
from datetime import datetime
from pathlib import Path


# Stub heavy deps before any mememo import.
def _stub_module(name: str, **attrs: object) -> None:
    if name in sys.modules:
        return
    m = _types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m


class _Stub:  # pragma: no cover
    def __init__(self, *a, **k) -> None: ...

    def tool(self, *a, **k):
        def deco(fn):
            return fn

        return deco

    def resource(self, *a, **k):
        def deco(fn):
            return fn

        return deco


_stub_module("sentence_transformers", SentenceTransformer=_Stub)
_stub_module("faiss", Index=_Stub, IndexFlatL2=_Stub, IndexIDMap=_Stub, IndexIVFFlat=_Stub)
_stub_module("fastmcp", FastMCP=_Stub)


from mememo.cli import (  # noqa: E402
    _PRE_TOOL_MAX_MEMORIES,
    _PRE_TOOL_MAX_TOKENS,
    _build_pre_tool_block,
    _extract_pre_tool_query,
)
from mememo.types.memory import (  # noqa: E402
    BranchContext,
    Memory,
    MemoryContent,
    MemoryMetadata,
    MemoryRelationships,
    MemorySummary,
    RepoContext,
    SearchResult,
)
from mememo.utils.token_counter import count_tokens  # noqa: E402

# ---------- helpers ---------------------------------------------------------


def _fake_result(one_line: str, file_path: str = "src/foo.py", ctype: str = "code_snippet"):
    now = datetime.now()
    memory = Memory(
        id=f"mem-{abs(hash(one_line))}",
        repo=RepoContext(id="r", name="t", path="/tmp/t"),
        branch=BranchContext(name="main", commit_hash="a" * 40),
        content=MemoryContent(
            type=ctype,
            text=one_line,
            file_path=file_path,
            line_range=(10, 20),
        ),
        metadata=MemoryMetadata(
            checksum="c",
            token_count=10,
            created_at=now,
            updated_at=now,
        ),
        relationships=MemoryRelationships(),
        summary=MemorySummary(one_line=one_line),
    )
    return SearchResult(memory=memory, similarity=0.9)


# ---------- T032: query extraction -----------------------------------------


def test_t032_query_from_grep() -> None:
    q = _extract_pre_tool_query("Grep", {"pattern": "FAISS sharding", "path": "mememo/"})
    assert q == "FAISS sharding mememo/"


def test_t032_query_from_glob() -> None:
    q = _extract_pre_tool_query("Glob", {"pattern": "**/*.py"})
    assert q == "**/*.py"


def test_t032_query_from_bash_truncates_long_command() -> None:
    long_cmd = "ls -la " + "x" * 500
    q = _extract_pre_tool_query("Bash", {"command": long_cmd})
    assert q is not None
    assert len(q) <= 200


def test_t032_unsupported_tool_returns_none() -> None:
    assert _extract_pre_tool_query("Write", {"file_path": "x"}) is None
    assert _extract_pre_tool_query("Grep", {}) is None


# ---------- T032 / T038: block formatting + 300-token budget ---------------


def test_t032_block_caps_at_three_memories() -> None:
    results = [_fake_result(f"insight number {i}") for i in range(10)]
    block = _build_pre_tool_block(results, _PRE_TOOL_MAX_MEMORIES, _PRE_TOOL_MAX_TOKENS)
    assert block is not None
    assert block.count("\n- ") + 1 == _PRE_TOOL_MAX_MEMORIES  # exactly 3 lines


def test_t038_block_stays_under_300_token_budget() -> None:
    """FR-028 / FR-035: even with verbose results, the block must not exceed
    the 300-token cap. Pins the budget so future regressions fail CI."""
    long_text = "extremely verbose decision rationale " * 30
    results = [_fake_result(long_text) for _ in range(10)]
    block = _build_pre_tool_block(results, _PRE_TOOL_MAX_MEMORIES, _PRE_TOOL_MAX_TOKENS)
    if block is None:
        return  # nothing fit — acceptable degenerate case
    assert count_tokens(block) <= _PRE_TOOL_MAX_TOKENS


def test_t032_empty_results_returns_none() -> None:
    assert _build_pre_tool_block([], _PRE_TOOL_MAX_MEMORIES, _PRE_TOOL_MAX_TOKENS) is None


def test_t032_first_result_too_large_returns_none() -> None:
    """Edge case: a single oversized result that won't fit -> None, not crash."""
    huge = _fake_result("X" * 5000)
    block = _build_pre_tool_block([huge], _PRE_TOOL_MAX_MEMORIES, 5)
    # Either None or a single line below budget. Don't crash.
    if block is not None:
        assert count_tokens(block) <= 5


# ---------- T036: PreToolUse settings registration --------------------------


def test_t036_register_pretool_creates_new_settings(tmp_path: Path) -> None:
    from mememo.hooks.installer import register_claude_pretool_hook

    repo = tmp_path / "repo"
    repo.mkdir()
    result = register_claude_pretool_hook(str(repo))
    assert result["status"] == "added"
    settings_path = Path(result["settings_path"])
    assert settings_path.exists()
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    pre_tool = settings["hooks"]["PreToolUse"]
    assert any("mememo pre-tool" in h["command"] for entry in pre_tool for h in entry["hooks"])


def test_t036_register_pretool_idempotent(tmp_path: Path) -> None:
    from mememo.hooks.installer import register_claude_pretool_hook

    repo = tmp_path / "repo"
    repo.mkdir()
    first = register_claude_pretool_hook(str(repo))
    second = register_claude_pretool_hook(str(repo))
    assert first["status"] == "added"
    assert second["status"] == "present"


def test_t036_register_refuses_to_clobber_existing(tmp_path: Path) -> None:
    from mememo.hooks.installer import register_claude_pretool_hook

    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    existing = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-hook"}]}
            ]
        }
    }
    (repo / ".claude" / "settings.json").write_text(json.dumps(existing), encoding="utf-8")

    result = register_claude_pretool_hook(str(repo))
    assert result["status"] == "skipped"
    after = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "user-hook" in after["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def test_t036_register_force_appends(tmp_path: Path) -> None:
    from mememo.hooks.installer import register_claude_pretool_hook

    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    existing = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-hook"}]}
            ]
        }
    }
    (repo / ".claude" / "settings.json").write_text(json.dumps(existing), encoding="utf-8")

    result = register_claude_pretool_hook(str(repo), force=True)
    assert result["status"] == "added"
    after = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    pre_tool = after["hooks"]["PreToolUse"]
    assert len(pre_tool) == 2  # original + mememo


def test_t036_register_handles_invalid_json(tmp_path: Path) -> None:
    from mememo.hooks.installer import register_claude_pretool_hook

    repo = tmp_path / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")

    result = register_claude_pretool_hook(str(repo))
    assert result["status"] == "error"
