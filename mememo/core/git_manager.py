"""
Git Manager for mememo.

Handles all git repository interactions with security-first approach.
Provides repo/branch detection and context management.
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Literal

from ..types import BranchContext, GitContext, RepoContext
from ..utils.hashing import hash_path

logger = logging.getLogger(__name__)

# Whitelist of allowed git commands for security.
# v0.4 adds merge-base (FR-006) and cat-file (used by is_merge_commit).
ALLOWED_GIT_COMMANDS = [
    "rev-parse",
    "branch",
    "config",
    "status",
    "diff",
    "log",
    "merge-base",
    "cat-file",
]

AllowedGitCommand = Literal[
    "rev-parse", "branch", "config", "status", "diff", "log", "merge-base", "cat-file"
]

# Hardened environment for every git invocation. mememo only ever runs local,
# read-only git commands, so none of these helpers are needed — and each is a way
# for git to spawn a long-lived grandchild (credential prompt, pager, fsmonitor
# daemon) that inherits the captured stdout pipe and outlives `git` itself. When
# that happens, subprocess.communicate() blocks forever draining the pipe and the
# `timeout` never fires — which is exactly what froze the MCP server's first-call
# init (a `rev-parse` stuck in communicate() for minutes past its 30s timeout).
_GIT_SAFE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",  # fail instead of prompting for credentials
    "GIT_OPTIONAL_LOCKS": "0",  # skip opportunistic index refresh / locks
    "GIT_PAGER": "cat",  # never spawn a pager (log/diff)
    "GCM_INTERACTIVE": "never",  # Git Credential Manager: no GUI/console prompt
}
# Per-invocation overrides that suppress daemon/helper grandchildren outright.
_GIT_SAFE_FLAGS = ["-c", "core.fsmonitor=", "-c", "credential.helper="]


class GitManager:
    """
    GitManager handles all git repository interactions.

    Security features:
    - Whitelisted git commands only
    - No shell execution (subprocess direct args)
    - Limited buffer sizes
    - Safe path handling
    """

    async def _exec_git(
        self, command: AllowedGitCommand, args: list[str], cwd: str | None = None
    ) -> str:
        """
        Execute a git command safely with whitelist validation.

        Args:
            command: Git command (must be in whitelist)
            args: Command arguments
            cwd: Working directory (defaults to current dir)

        Returns:
            Command output (stdout)

        Raises:
            ValueError: If command not in whitelist
            RuntimeError: If git command fails
        """
        if command not in ALLOWED_GIT_COMMANDS:
            raise ValueError(f"SECURITY: Git command '{command}' not allowed")

        # `config` is a pure local key/value read-write that spawns no helper
        # grandchildren, so it doesn't need the daemon-suppressing -c overrides —
        # and skipping them avoids shadowing a `config --get core.fsmonitor` /
        # `credential.helper` read with the override's empty value.
        flags = [] if command == "config" else _GIT_SAFE_FLAGS
        try:
            result = subprocess.run(
                ["git", *flags, command, *args],
                cwd=cwd or os.getcwd(),
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
                # Detach from the parent's stdin (the MCP stdio pipe) so git can
                # never block reading it, and harden the env so no interactive or
                # daemon helper can spawn and wedge the captured pipe.
                stdin=subprocess.DEVNULL,
                env={**os.environ, **_GIT_SAFE_ENV},
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Git command failed: {e.stderr}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("Git command timed out after 30s")

    async def canonical_repo_root(self, cwd: str | None = None) -> str:
        """Return the canonical repo root, collapsing linked worktrees (FR-024).

        Uses ``git rev-parse --git-common-dir`` so that worktrees created via
        ``git worktree add`` resolve to the same path as the primary checkout.
        Falls back to ``find_repo_root`` when the git-common-dir output is
        ``.git`` (the legacy single-checkout case).
        """
        try:
            common_dir = await self._exec_git("rev-parse", ["--git-common-dir"], cwd)
        except RuntimeError:
            return await self.find_repo_root(cwd)
        # git prints either a path ending in `.git` (canonical case) or just
        # `.git` (relative to repo root). Strip and normalize.
        common_path = Path(common_dir)
        if not common_path.is_absolute():
            # Resolve relative to cwd.
            common_path = (Path(cwd or os.getcwd()) / common_dir).resolve()
        if common_path.name == ".git":
            common_path = common_path.parent
        return str(common_path)

    async def find_repo_root(self, cwd: str | None = None) -> str:
        """
        Find the root directory of the git repository.

        Args:
            cwd: Starting directory (defaults to current dir)

        Returns:
            Absolute path to repository root

        Raises:
            RuntimeError: If not in a git repository
        """
        try:
            repo_root = await self._exec_git("rev-parse", ["--show-toplevel"], cwd)
            return repo_root
        except RuntimeError:
            current_dir = cwd or os.getcwd()
            raise RuntimeError(
                f"Not a git repository (or any of the parent directories). "
                f"Current directory: {current_dir}"
            )

    async def get_current_branch(self, cwd: str | None = None) -> str:
        """
        Get the current branch name.

        Args:
            cwd: Working directory

        Returns:
            Branch name or 'detached-<sha>' if in detached HEAD state
        """
        try:
            # Try to get current branch name
            branch = await self._exec_git("branch", ["--show-current"], cwd)

            if branch:
                return branch

            # Fallback: check if we're in detached HEAD state
            head_ref = await self._exec_git("rev-parse", ["--abbrev-ref", "HEAD"], cwd)

            if head_ref == "HEAD":
                # Detached HEAD - use commit SHA
                commit_sha = await self.get_latest_commit(cwd)
                return f"detached-{commit_sha[:8]}"

            return head_ref
        except RuntimeError as e:
            raise RuntimeError(f"Failed to get current branch: {e}")

    async def get_latest_commit(self, cwd: str | None = None) -> str:
        """
        Get the latest commit SHA.

        Args:
            cwd: Working directory

        Returns:
            Commit SHA (full hash)
        """
        try:
            commit_sha = await self._exec_git("rev-parse", ["HEAD"], cwd)
            return commit_sha
        except RuntimeError as e:
            raise RuntimeError(f"Failed to get latest commit: {e}")

    async def get_remote_url(self, cwd: str | None = None) -> str | None:
        """
        Get the git remote URL (if configured).

        Args:
            cwd: Working directory

        Returns:
            Remote URL or None if not configured
        """
        try:
            remote_url = await self._exec_git("config", ["--get", "remote.origin.url"], cwd)
            return remote_url if remote_url else None
        except RuntimeError:
            # No remote configured - that's okay
            return None

    async def detect_context(self, cwd: str | None = None) -> GitContext:
        """
        Detect the current git context (repo + branch).

        This is called on every MCP tool invocation to ensure correct isolation.

        Args:
            cwd: Working directory

        Returns:
            GitContext with repo and branch information

        Raises:
            RuntimeError: If git context cannot be detected
        """
        working_dir = cwd or os.getcwd()

        try:
            # v0.6 (FR-024): use canonical_repo_root so linked worktrees share
            # the same repo_id as the primary checkout.
            repo_path = await self.canonical_repo_root(working_dir)

            # Get repo name from path
            repo_name = Path(repo_path).name

            # Get remote URL before resolving id (URL is an input to the resolver)
            remote_url = await self.get_remote_url(repo_path)

            # portable identity (wave 0b): resolve via env > .mememo/project.yaml
            # > remote-url hash > path hash, instead of bare hash_path.
            from .identity import resolve_project_id
            from .project_config import load_project_config

            repo_id = resolve_project_id(
                repo_path=repo_path,
                remote_url=remote_url,
                project_config=load_project_config(repo_path),
            )

            # Get branch info
            branch_name = await self.get_current_branch(repo_path)
            commit_hash = await self.get_latest_commit(repo_path)

            repo = RepoContext(
                id=repo_id,
                name=repo_name,
                path=repo_path,
                remote_url=remote_url,
            )

            branch = BranchContext(
                name=branch_name,
                commit_hash=commit_hash,
            )

            return GitContext(repo=repo, branch=branch)
        except Exception:
            resolved = str(Path(working_dir).resolve())
            logger.info("Not in a git repository (%s) - using default context", resolved)
            return GitContext(
                repo=RepoContext(
                    id=hash_path(resolved),
                    name=Path(resolved).name,
                    path=resolved,
                    remote_url=None,
                ),
                branch=BranchContext(name="main", commit_hash=""),
            )

    async def is_git_repo(self, cwd: str | None = None) -> bool:
        """
        Check if a directory is inside a git repository.

        Args:
            cwd: Directory to check

        Returns:
            True if in a git repository
        """
        try:
            await self.find_repo_root(cwd)
            return True
        except RuntimeError:
            return False

    async def get_repo_id(self, cwd: str | None = None) -> str:
        """
        Get repository ID for a given path.

        v0.6: uses canonical_repo_root so linked worktrees collapse to a
        single repo_id (FR-024).

        Args:
            cwd: Working directory

        Returns:
            Stable repository ID (SHA-256 hash of canonical path)
        """
        repo_path = await self.canonical_repo_root(cwd)
        remote_url = await self.get_remote_url(repo_path)
        from .identity import resolve_project_id
        from .project_config import load_project_config

        return resolve_project_id(
            repo_path=repo_path,
            remote_url=remote_url,
            project_config=load_project_config(repo_path),
        )

    # ----- v0.4.0 commit-aware extensions (FR-006) --------------------------

    async def merge_base(self, branch_a: str, branch_b: str, cwd: str | None = None) -> str | None:
        """Return the SHA of the merge-base between two refs, or None if disjoint."""
        try:
            sha = await self._exec_git("merge-base", [branch_a, branch_b], cwd)
            return sha or None
        except RuntimeError:
            # `git merge-base` exits 1 when there is no common ancestor; that's
            # not an error condition for callers, just "no merge-base."
            return None

    async def is_merge_commit(self, sha: str, cwd: str | None = None) -> bool:
        """A commit is a merge if it has more than one parent."""
        try:
            parents = await self._exec_git("rev-parse", [f"{sha}^@"], cwd)
        except RuntimeError:
            return False
        # `<sha>^@` lists all parents one-per-line; merges have >=2.
        return len([line for line in parents.split("\n") if line.strip()]) >= 2

    async def diff_between(self, base: str, head: str, cwd: str | None = None) -> dict[str, str]:
        """Return {file_path: change_kind} between base..head.

        change_kind is one of 'A' (added), 'M' (modified), 'D' (deleted),
        'R' (renamed), 'T' (type-change), 'C' (copied). Wraps
        `git diff --name-status` so callers don't have to parse stdout.
        """
        try:
            # Append '--' so git interprets the next token as a revision range,
            # never an option (security audit 2026-05-13).
            output = await self._exec_git("diff", ["--name-status", f"{base}..{head}", "--"], cwd)
        except RuntimeError as e:
            raise RuntimeError(f"diff_between({base!r}, {head!r}) failed: {e}")
        out: dict[str, str] = {}
        for line in output.split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            # Rename / copy lines look like "R100\told\tnew" — record the new path.
            kind = parts[0][:1]
            path = parts[-1]
            out[path] = kind
        return out

    # ------------------------------------------------------------------------

    async def get_changed_files(
        self, from_commit: str, to_commit: str, cwd: str | None = None
    ) -> list[str]:
        """
        Get list of changed files between two commits.

        Useful for incremental indexing.

        Args:
            from_commit: Starting commit
            to_commit: Ending commit
            cwd: Working directory

        Returns:
            List of changed file paths
        """
        try:
            output = await self._exec_git(
                "diff", ["--name-only", f"{from_commit}..{to_commit}"], cwd
            )

            if not output:
                return []

            return [line for line in output.split("\n") if line.strip()]
        except RuntimeError as e:
            raise RuntimeError(f"Failed to get changed files: {e}")
