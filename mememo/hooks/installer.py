"""Opt-in git hook installer for mememo (T013 / T014 / T036 / FR-033).

`mememo install-git-hooks --repo-path <path>` copies the bundled
post-merge and post-commit scripts into .git/hooks/. Refuses to clobber
existing hook files unless --force is passed.

v0.6 (T036): the installer also knows how to register the PreToolUse
hook into a project's `.claude/settings.json` so Grep/Glob/Bash tool
calls get augmented by mememo without manual config. This step is
opt-in via `register_claude_code=True`.
"""

from __future__ import annotations

import json
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


def register_claude_pretool_hook(repo_path: str, force: bool = False) -> dict:
    """Add a PreToolUse mememo entry to ``<repo>/.claude/settings.json``.

    Idempotent: if a matching entry is already present, return it unchanged.
    Refuses to overwrite a user-customised PreToolUse block unless
    ``force=True`` — the user's block can be a list of dicts that already
    references different commands, and we don't want to silently drop them.

    Returns a dict with keys ``status`` (added / present / skipped /
    error), ``settings_path`` and the merged settings.
    """
    repo = Path(repo_path).resolve()
    settings_path = repo / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            return {
                "status": "error",
                "settings_path": str(settings_path),
                "reason": "settings.json is not valid JSON",
            }
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})
    pre_tool_matchers = hooks.setdefault("PreToolUse", [])

    mememo_entry = {
        "matcher": "Grep|Glob|Bash",
        "hooks": [{"type": "command", "command": "python -m mememo pre-tool --hook"}],
    }

    # Detect an existing mememo entry by scanning the command strings.
    def _has_mememo(entry: dict) -> bool:
        for h in entry.get("hooks", []):
            if isinstance(h, dict) and "mememo pre-tool" in (h.get("command") or ""):
                return True
        return False

    for existing in pre_tool_matchers:
        if isinstance(existing, dict) and _has_mememo(existing):
            return {"status": "present", "settings_path": str(settings_path)}

    if pre_tool_matchers and not force:
        # User already has PreToolUse entries; don't silently shove ours in.
        return {
            "status": "skipped",
            "settings_path": str(settings_path),
            "reason": "PreToolUse already configured; rerun with --force to append",
        }

    pre_tool_matchers.append(mememo_entry)
    settings_path.write_text(json.dumps(settings, indent=2, sort_keys=True), encoding="utf-8")
    return {"status": "added", "settings_path": str(settings_path)}


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
