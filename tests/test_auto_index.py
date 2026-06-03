"""Tests for the `mememo index` CLI registration + auto-index-on-session-start."""

from __future__ import annotations


def test_index_subcommand_registered() -> None:
    from mememo.__main__ import _SUBCOMMANDS

    assert "index" in _SUBCOMMANDS


def test_auto_index_config_flag(monkeypatch) -> None:
    from mememo.types.config import MemoConfig

    monkeypatch.delenv("MEMEMO_AUTO_INDEX_ON_SESSION_START", raising=False)
    assert MemoConfig.from_env().hook.auto_index_on_session_start is False

    monkeypatch.setenv("MEMEMO_AUTO_INDEX_ON_SESSION_START", "true")
    cfg = MemoConfig.from_env()
    assert cfg.hook.auto_index_on_session_start is True


def test_maybe_background_index_spawns_then_respects_lock(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "store"))
    (tmp_path / ".git").mkdir()  # make the fallback treat tmp_path as a repo
    from mememo.commands import session_start as ss
    from mememo.types.config import MemoConfig

    cfg = MemoConfig.from_env()
    calls: list[list[str]] = []

    ss._maybe_background_index(cfg, str(tmp_path), spawn=lambda argv: calls.append(argv))
    assert len(calls) == 1
    argv = calls[0]
    assert "index" in argv and argv[-1] == "--quiet"
    assert "mememo" in argv

    # A second call within the TTL window must NOT spawn again (lock guard).
    calls.clear()
    ss._maybe_background_index(cfg, str(tmp_path), spawn=lambda argv: calls.append(argv))
    assert calls == []


def test_maybe_background_index_noop_for_non_git_dir(tmp_path, monkeypatch) -> None:
    # A directory that isn't a git repo (and has no child repos) must not be
    # indexed — that would pollute the global lane.
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "store"))
    import mememo.core.workspace as ws
    from mememo.commands import session_start as ss
    from mememo.types.config import MemoConfig

    monkeypatch.setattr(ws, "discover_workspace", lambda _cwd: [])
    cfg = MemoConfig.from_env()
    calls: list[list[str]] = []
    ss._maybe_background_index(cfg, str(tmp_path), spawn=lambda argv: calls.append(argv))
    assert calls == []  # no .git -> no spawn


def test_maybe_background_index_releases_lock_on_spawn_failure(tmp_path, monkeypatch) -> None:
    # A failed spawn must release the lock so the next session can retry.
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "store"))
    (tmp_path / ".git").mkdir()
    from mememo.commands import session_start as ss
    from mememo.types.config import MemoConfig

    cfg = MemoConfig.from_env()

    def _boom(_argv):
        raise OSError("spawn failed")

    ss._maybe_background_index(cfg, str(tmp_path), spawn=_boom)

    # Lock released -> a subsequent call spawns again instead of being suppressed.
    calls: list[list[str]] = []
    ss._maybe_background_index(cfg, str(tmp_path), spawn=lambda argv: calls.append(argv))
    assert len(calls) == 1
