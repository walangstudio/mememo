"""Tests for mememo.core.workspace — discovery + installer SessionStart."""

from __future__ import annotations

import json
import sys
import types as _types
from pathlib import Path

# ---------------------------------------------------------------------------
# Stub heavy deps before any mememo import.
# ---------------------------------------------------------------------------


def _stub_module(name: str, **attrs: object) -> None:
    if name in sys.modules:
        return
    m = _types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m


class _Stub:
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_git_repo(base: Path, name: str) -> Path:
    """Create a minimal fake git repo under base/name."""
    repo = base / name
    (repo / ".git").mkdir(parents=True)
    return repo


# ---------------------------------------------------------------------------
# discover_workspace — unit tests for _discover_workspace_capped
# ---------------------------------------------------------------------------


from mememo.core.workspace import _discover_workspace_capped  # noqa: E402


def _no_ws_cfg(path):
    return {}


class TestDiscoverWorkspace:
    def test_single_repo_cwd_returns_self(self, tmp_path):
        repo = _make_git_repo(tmp_path, "myrepo")
        (repo / ".git").mkdir(exist_ok=True)
        result = _discover_workspace_capped(
            str(repo), max_repos=8, load_workspace_config_fn=_no_ws_cfg
        )
        assert result == [str(repo)]

    def test_parent_with_two_child_repos(self, tmp_path):
        r1 = _make_git_repo(tmp_path, "repo1")
        r2 = _make_git_repo(tmp_path, "repo2")
        result = _discover_workspace_capped(
            str(tmp_path), max_repos=8, load_workspace_config_fn=_no_ws_cfg
        )
        assert set(result) == {str(r1), str(r2)}

    def test_cap_respected(self, tmp_path):
        for i in range(5):
            _make_git_repo(tmp_path, f"repo{i}")
        result = _discover_workspace_capped(
            str(tmp_path), max_repos=3, load_workspace_config_fn=_no_ws_cfg
        )
        assert len(result) == 3

    def test_non_repo_no_children_returns_empty(self, tmp_path):
        # tmp_path has no .git and no children with .git
        result = _discover_workspace_capped(
            str(tmp_path), max_repos=8, load_workspace_config_fn=_no_ws_cfg
        )
        assert result == []

    def test_workspace_yaml_extra_paths_included(self, tmp_path):
        extra = _make_git_repo(tmp_path, "extra_repo")

        def _ws_cfg(path):
            return {"projects": [str(extra)]}

        result = _discover_workspace_capped(
            str(tmp_path), max_repos=8, load_workspace_config_fn=_ws_cfg
        )
        assert str(extra) in result

    def test_workspace_yaml_capped_together(self, tmp_path):
        for i in range(3):
            _make_git_repo(tmp_path, f"child{i}")
        extra1 = _make_git_repo(tmp_path, "extra1")
        extra2 = _make_git_repo(tmp_path, "extra2")

        def _ws_cfg(path):
            return {"projects": [str(extra1), str(extra2)]}

        result = _discover_workspace_capped(
            str(tmp_path), max_repos=4, load_workspace_config_fn=_ws_cfg
        )
        assert len(result) == 4


# ---------------------------------------------------------------------------
# installer — register_claude_session_start_hook
# ---------------------------------------------------------------------------


from mememo.hooks.installer import register_claude_session_start_hook  # noqa: E402


class TestRegisterSessionStartHook:
    def test_adds_entry_to_new_settings(self, tmp_path):
        result = register_claude_session_start_hook(str(tmp_path))
        assert result["status"] == "added"
        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        ss_hooks = settings["hooks"]["SessionStart"]
        assert any(
            "mememo session-start" in h.get("command", "")
            for entry in ss_hooks
            for h in entry.get("hooks", [])
        )

    def test_idempotent_present(self, tmp_path):
        register_claude_session_start_hook(str(tmp_path))
        result = register_claude_session_start_hook(str(tmp_path))
        assert result["status"] == "present"

    def test_skips_when_existing_other_entry(self, tmp_path):
        settings = {
            "hooks": {
                "SessionStart": [{"hooks": [{"type": "command", "command": "some-other-tool"}]}]
            }
        }
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps(settings))
        result = register_claude_session_start_hook(str(tmp_path))
        assert result["status"] == "skipped"

    def test_force_appends_despite_existing(self, tmp_path):
        settings = {
            "hooks": {
                "SessionStart": [{"hooks": [{"type": "command", "command": "some-other-tool"}]}]
            }
        }
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps(settings))
        result = register_claude_session_start_hook(str(tmp_path), force=True)
        assert result["status"] == "added"

    def test_error_on_invalid_json(self, tmp_path):
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text("{ not valid json }")
        result = register_claude_session_start_hook(str(tmp_path))
        assert result["status"] == "error"

    def test_async_flag_set(self, tmp_path):
        register_claude_session_start_hook(str(tmp_path))
        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        ss_hooks = settings["hooks"]["SessionStart"]
        hook_cmds = [h for entry in ss_hooks for h in entry.get("hooks", [])]
        assert any(h.get("async") is True for h in hook_cmds)
