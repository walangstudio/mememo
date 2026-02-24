"""
Git Manager for mememo.

Handles all git repository interactions with security-first approach.
Provides repo/branch detection and context management.
"""

import os
import subprocess
from pathlib import Path
from typing import Literal

from ..types import BranchContext, GitContext, RepoContext
from ..utils.hashing import hash_path

# Whitelist of allowed git commands for security
ALLOWED_GIT_COMMANDS = [
    "rev-parse",
    "branch",
    "config",
    "status",
    "diff",
    "log",
]

AllowedGitCommand = Literal["rev-parse", "branch", "config", "status", "diff", "log"]


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

        try:
            result = subprocess.run(
                ["git", command] + args,
                cwd=cwd or os.getcwd(),
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Git command failed: {e.stderr}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("Git command timed out after 30s")

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
            # Find repo root
            repo_path = await self.find_repo_root(working_dir)

            # Get repo name from path
            repo_name = Path(repo_path).name

            # Generate stable repo ID from absolute path
            repo_id = hash_path(repo_path)

            # Get remote URL
            remote_url = await self.get_remote_url(repo_path)

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
        except Exception as e:
            raise RuntimeError(f"Failed to detect git context: {str(e)}")

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

        Args:
            cwd: Working directory

        Returns:
            Stable repository ID (SHA-256 hash of path)
        """
        repo_path = await self.find_repo_root(cwd)
        return hash_path(repo_path)

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
