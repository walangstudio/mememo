"""CLI wiring for `mememo curate-skills` (headless / cron curation)."""

from __future__ import annotations

import pytest


def test_curate_skills_registered() -> None:
    from mememo.__main__ import _SUBCOMMANDS

    assert "curate-skills" in _SUBCOMMANDS


def test_curate_skills_help_exits_zero() -> None:
    from mememo.__main__ import _cmd_curate_skills

    with pytest.raises(SystemExit) as exc:
        _cmd_curate_skills(["--help"])
    assert exc.value.code == 0


def test_curate_skills_listed_in_help() -> None:
    from mememo.__main__ import _subcommand_help

    assert "consolidate" in _subcommand_help("curate-skills").lower()


def test_curate_cli_releases_lock_on_failure(tmp_path, monkeypatch) -> None:
    # A crashed background curate child must release its lock so the next session
    # retries instead of waiting out the interval. Force a failure via ensure_initialized.
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "store"))
    import mememo.server as srv

    async def _boom():
        raise RuntimeError("init failed")

    monkeypatch.setattr(srv, "ensure_initialized", _boom)
    from mememo.__main__ import _cmd_curate_skills

    lock = tmp_path / "curate.lock"
    lock.write_text("x")
    rc = _cmd_curate_skills(["--apply", "--lock", str(lock)])
    assert rc == 1
    assert not lock.exists()
