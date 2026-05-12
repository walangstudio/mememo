"""Opt-in git hook installer for mememo (T013 / T014 / FR-033).

`mememo install-git-hooks --repo-path <path>` copies the bundled
post-merge and post-commit scripts into .git/hooks/. Refuses to clobber
existing hook files unless --force is passed.
"""

from __future__ import annotations

import shutil
import stat
from pathlib import Path

# Hook scripts ship under mememo/hooks/ next to this module.
_HOOK_SOURCE_DIR = Path(__file__).resolve().parent
HOOK_NAMES = ("post-merge", "post-commit")


class HookInstallResult:
    def __init__(self) -> None:
        self.installed: list[str] = []
        self.skipped_existing: list[str] = []
        self.errors: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.errors

    def report(self) -> str:
        parts: list[str] = []
        if self.installed:
            parts.append(f"installed: {', '.join(self.installed)}")
        if self.skipped_existing:
            parts.append(
                f"skipped (already present, pass --force to overwrite): "
                f"{', '.join(self.skipped_existing)}"
            )
        if self.errors:
            parts.append(f"errors: {'; '.join(self.errors)}")
        return " | ".join(parts) if parts else "no hooks processed"


def install_git_hooks(repo_path: str, force: bool = False) -> HookInstallResult:
    """Copy bundled hooks into <repo_path>/.git/hooks/.

    The installer refuses to overwrite an existing hook file unless
    force=True so user customisations are never silently clobbered.
    """
    result = HookInstallResult()
    repo = Path(repo_path).resolve()
    git_dir = repo / ".git"
    if not git_dir.is_dir():
        result.errors.append(f"not a git repository: {repo}")
        return result

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    for name in HOOK_NAMES:
        src = _HOOK_SOURCE_DIR / name
        if not src.is_file():
            result.errors.append(f"bundled hook missing: {src}")
            continue
        dst = hooks_dir / name
        if dst.exists() and not force:
            result.skipped_existing.append(name)
            continue
        shutil.copyfile(src, dst)
        # +x for owner / group / others; same as `chmod 755` on POSIX.
        dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        result.installed.append(name)

    return result
