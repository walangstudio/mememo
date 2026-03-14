"""
Tests for config path expansion across all operating systems.

Covers:
- str with tilde  (original gap — validator skipped Path inputs)
- Path with tilde (what from_env() actually passes — the real bug)
- Absolute paths  (must pass through unchanged)
- POSIX home mock (Linux/macOS)
- Windows home mock (USERPROFILE / HOMEDRIVE+HOMEPATH)
- MEMEMO_STORAGE_DIR / MEMEMO_DATA_DIR env var overrides
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from mememo.types.config import Config, StorageConfig


# ---------------------------------------------------------------------------
# Validator-level tests
# ---------------------------------------------------------------------------


def test_str_tilde_expands():
    sc = StorageConfig(base_dir="~/.mememo")
    assert sc.base_dir.is_absolute()
    assert "~" not in str(sc.base_dir)


def test_path_tilde_expands():
    """This was the actual bug: Path objects bypassed expanduser."""
    sc = StorageConfig(base_dir=Path("~/.mememo"))
    assert sc.base_dir.is_absolute()
    assert "~" not in str(sc.base_dir)


def test_absolute_str_passes_through():
    abs_path = str(Path.home() / "custom" / "mememo")
    sc = StorageConfig(base_dir=abs_path)
    assert sc.base_dir.is_absolute()
    assert sc.base_dir == Path(abs_path).resolve()


def test_absolute_path_passes_through():
    abs_path = Path.home() / "custom" / "mememo"
    sc = StorageConfig(base_dir=abs_path)
    assert sc.base_dir.is_absolute()


# ---------------------------------------------------------------------------
# POSIX home simulation (Linux / macOS)
# ---------------------------------------------------------------------------


def test_posix_home_via_env(tmp_path):
    """On POSIX, expanduser resolves ~ using the HOME env var."""
    fake_home = str(tmp_path / "fakehome")
    with patch.dict(os.environ, {"HOME": fake_home}, clear=False):
        sc = StorageConfig(base_dir="~/.mememo")
        assert sc.base_dir.is_absolute()
        assert "~" not in str(sc.base_dir)
        # On POSIX, HOME is authoritative — verify the path is actually under fake_home.
        # On Windows, expanduser() uses USERPROFILE, not HOME, so we skip this check.
        if sys.platform != "win32":
            assert str(sc.base_dir).startswith(fake_home)


# ---------------------------------------------------------------------------
# Windows home simulation (USERPROFILE and HOMEDRIVE+HOMEPATH)
# ---------------------------------------------------------------------------


def test_windows_userprofile_expansion(tmp_path):
    """
    On Windows, expanduser falls back to USERPROFILE.
    We simulate this by temporarily overriding it.
    """
    fake_home = str(tmp_path / "winuser")
    env_patch = {"USERPROFILE": fake_home, "HOME": fake_home}
    with patch.dict(os.environ, env_patch, clear=False):
        sc = StorageConfig(base_dir=Path("~/.mememo"))
        assert sc.base_dir.is_absolute()
        assert "~" not in str(sc.base_dir)


# ---------------------------------------------------------------------------
# from_env() — env var overrides
# ---------------------------------------------------------------------------


def test_from_env_default_resolves(monkeypatch):
    """Default path (~/.mememo/data) is expanded even without env vars."""
    monkeypatch.delenv("MEMEMO_STORAGE_DIR", raising=False)
    monkeypatch.delenv("MEMEMO_DATA_DIR", raising=False)
    cfg = Config.from_env()
    assert cfg.storage.base_dir.is_absolute()
    assert "~" not in str(cfg.storage.base_dir)


def test_from_env_storage_dir_absolute(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", str(tmp_path / "custom"))
    monkeypatch.delenv("MEMEMO_DATA_DIR", raising=False)
    cfg = Config.from_env()
    assert cfg.storage.base_dir.is_absolute()
    assert cfg.storage.base_dir == (tmp_path / "custom").resolve()


def test_from_env_storage_dir_tilde(monkeypatch):
    monkeypatch.setenv("MEMEMO_STORAGE_DIR", "~/.mememo/custom")
    monkeypatch.delenv("MEMEMO_DATA_DIR", raising=False)
    cfg = Config.from_env()
    assert cfg.storage.base_dir.is_absolute()
    assert "~" not in str(cfg.storage.base_dir)


def test_from_env_data_dir_fallback_tilde(monkeypatch):
    """MEMEMO_DATA_DIR (legacy) is also expanded correctly."""
    monkeypatch.delenv("MEMEMO_STORAGE_DIR", raising=False)
    monkeypatch.setenv("MEMEMO_DATA_DIR", "~/.mememo/legacy")
    cfg = Config.from_env()
    assert cfg.storage.base_dir.is_absolute()
    assert "~" not in str(cfg.storage.base_dir)


# ---------------------------------------------------------------------------
# Idempotency — calling resolve() on an already-resolved path is a no-op
# ---------------------------------------------------------------------------


def test_already_resolved_path_unchanged(tmp_path):
    sc = StorageConfig(base_dir=tmp_path)
    assert sc.base_dir == tmp_path.resolve()
