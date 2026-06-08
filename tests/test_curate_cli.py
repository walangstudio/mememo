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
