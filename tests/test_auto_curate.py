"""Tests for autonomous skill curation on session start (Hermes-style periodic curator)."""

from __future__ import annotations


def test_auto_curate_config_flags(monkeypatch) -> None:
    from mememo.types.config import MemoConfig

    for var in (
        "MEMEMO_AUTO_CURATE_ON_SESSION_START",
        "MEMEMO_AUTO_CURATE_MIN_INTERVAL_HOURS",
        "MEMEMO_AUTO_CURATE_STALE_UNUSED_DAYS",
    ):
        monkeypatch.delenv(var, raising=False)
    h = MemoConfig.from_env().hook
    assert h.auto_curate_on_session_start is False
    assert h.auto_curate_min_interval_hours == 24.0
    assert h.auto_curate_stale_unused_days == 0

    monkeypatch.setenv("MEMEMO_AUTO_CURATE_ON_SESSION_START", "true")
    monkeypatch.setenv("MEMEMO_AUTO_CURATE_STALE_UNUSED_DAYS", "30")
    h = MemoConfig.from_env().hook
    assert h.auto_curate_on_session_start is True
    assert h.auto_curate_stale_unused_days == 30


def test_maybe_background_curate_spawns_then_respects_lock(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "store"))
    from mememo.commands import session_start as ss
    from mememo.types.config import MemoConfig

    cfg = MemoConfig.from_env()
    calls: list[list[str]] = []

    ss._maybe_background_curate(cfg, spawn=lambda argv: calls.append(argv))
    assert len(calls) == 1
    argv = calls[0]
    assert "curate-skills" in argv and "--apply" in argv and "mememo" in argv
    assert "--stale-days" not in argv  # default 0 -> dedup only
    # The child is handed its lock so it can release it on its own failure.
    assert "--lock" in argv and argv[argv.index("--lock") + 1].endswith(".lock")

    # A second call within the interval must NOT spawn again (lock guard).
    calls.clear()
    ss._maybe_background_curate(cfg, spawn=lambda argv: calls.append(argv))
    assert calls == []


def test_maybe_background_curate_passes_stale_days(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("MEMEMO_AUTO_CURATE_STALE_UNUSED_DAYS", "30")
    from mememo.commands import session_start as ss
    from mememo.types.config import MemoConfig

    cfg = MemoConfig.from_env()
    calls: list[list[str]] = []
    ss._maybe_background_curate(cfg, spawn=lambda argv: calls.append(argv))
    assert calls and "--stale-days" in calls[0]
    assert calls[0][calls[0].index("--stale-days") + 1] == "30"


def test_maybe_background_curate_releases_lock_on_spawn_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "store"))
    from mememo.commands import session_start as ss
    from mememo.types.config import MemoConfig

    cfg = MemoConfig.from_env()

    def _boom(_argv):
        raise OSError("spawn failed")

    ss._maybe_background_curate(cfg, spawn=_boom)

    # Lock released -> a subsequent call spawns again instead of being suppressed.
    calls: list[list[str]] = []
    ss._maybe_background_curate(cfg, spawn=lambda argv: calls.append(argv))
    assert len(calls) == 1
