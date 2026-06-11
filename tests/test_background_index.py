"""The MCP index_repository tool spawns a detached index and returns at once.

A full index is many seconds of CPU embedding; running it inline as one MCP tool
call holds the caller's turn open and reads as a hang. spawn_background_index forks
it into a detached process and returns immediately — these tests pin that contract.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from mememo.chunking.language_detector import get_index_globs
from mememo.tools.index_repository import spawn_background_index
from mememo.tools.schemas import IndexRepositoryParams


class _FakeProc:
    pid = 4242


@pytest.fixture
def captured_popen(monkeypatch):
    calls: dict = {}

    def fake_popen(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return calls


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n")
    return repo


def test_spawn_returns_immediately_and_detaches(tmp_path, captured_popen):
    repo = _repo(tmp_path)
    logs = tmp_path / "logs"
    params = IndexRepositoryParams(repo_path=str(repo), incremental=False)

    resp = spawn_background_index(params, log_dir=logs, python_exe="py")

    assert resp.success
    assert "background" in resp.message.lower()
    # An immediate "started" response — the real counts come later via check_memory.
    assert resp.files_indexed == 0 and resp.chunks_created == 0 and resp.duration_seconds == 0.0
    argv = captured_popen["argv"]
    assert argv[:4] == ["py", "-m", "mememo", "index"]
    assert str(repo.resolve()) in argv
    assert "--full" in argv  # incremental=False
    assert (logs / "index.log").exists()  # output goes to a tailable log
    kw = captured_popen["kwargs"]
    assert kw["stdin"] is subprocess.DEVNULL
    if sys.platform == "win32":  # detached so it outlives the call
        assert kw.get("creationflags")
    else:
        assert kw.get("start_new_session") is True


def test_spawn_incremental_has_no_full_flag(tmp_path, captured_popen):
    repo = _repo(tmp_path)
    params = IndexRepositoryParams(repo_path=str(repo), incremental=True)
    resp = spawn_background_index(params, log_dir=tmp_path / "logs")
    assert resp.success
    assert "--full" not in captured_popen["argv"]


def test_spawn_missing_repo_returns_error_without_spawning(tmp_path, captured_popen):
    params = IndexRepositoryParams(repo_path=str(tmp_path / "nope"))
    resp = spawn_background_index(params, log_dir=tmp_path / "logs")
    assert resp.success is False
    assert "argv" not in captured_popen  # never reached subprocess


def test_spawn_forwards_only_custom_patterns(tmp_path, captured_popen):
    repo = _repo(tmp_path)
    # Default pattern set → the CLI's own default applies, no --patterns noise.
    spawn_background_index(
        IndexRepositoryParams(repo_path=str(repo), file_patterns=list(get_index_globs())),
        log_dir=tmp_path / "l1",
    )
    assert "--patterns" not in captured_popen["argv"]
    # Caller-overridden patterns are forwarded.
    spawn_background_index(
        IndexRepositoryParams(repo_path=str(repo), file_patterns=["*.weird"]),
        log_dir=tmp_path / "l2",
    )
    assert "--patterns" in captured_popen["argv"]
    assert "*.weird" in captured_popen["argv"]
