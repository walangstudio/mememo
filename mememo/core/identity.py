"""
Project identity resolution for mememo.

Resolves a stable repo_id from multiple sources in priority order:
1. MEMEMO_REPO_ID env var (explicit override)
2. .mememo/project.yaml project_id field
3. Hash of normalized remote URL (portable across clones)
4. Hash of local repo path (fallback)
"""

import hashlib
import logging
import os
import re

logger = logging.getLogger(__name__)

# GLOBAL_REPO_ID is defined in types/memory.py to avoid a circular import
# (types -> core -> types via core/__init__ -> git_manager -> types).
# Import it here so callers can use core.identity.GLOBAL_REPO_ID as expected.
from ..types.memory import GLOBAL_REPO_ID as GLOBAL_REPO_ID  # noqa: E402

# Match scheme + optional user@ prefix
_SCHEME_USER_RE = re.compile(r"^(?:https?|ssh|git)://(?:[^@]+@)?")
# Match git@ host: prefix (SCP-style: git@github.com:owner/repo).
# Negative lookahead so a scheme URL (ssh://user@host:port/path) is NOT
# misread as SCP — otherwise the port becomes the "owner" segment.
_SCP_RE = re.compile(r"^(?!(?:https?|ssh|git)://)[^@]+@([^:]+):(.+)$")
# Map known SSH host aliases to their canonical hostname
_HOST_ALIASES: dict[str, str] = {
    "gh-kitty": "github.com",
    "gh-niny0": "github.com",
    "gh-nino": "github.com",
}


def normalize_remote(remote_url: str | None) -> str | None:
    """Strip scheme, user, host, leading slash, and .git suffix from a remote URL.

    Returns "owner/repo" (lowercase), or None when the input is absent/unparseable.

    Both SCP-style (git@github.com:owner/repo) and HTTPS-style
    (https://github.com/owner/repo) normalize to the same output, and SSH
    host aliases resolve to their canonical hostnames before stripping.
    """
    if not remote_url:
        return None

    url = remote_url.strip()

    # SCP-style: git@host:path
    m = _SCP_RE.match(url)
    if m:
        path = m.group(2)
    else:
        # Strip scheme + optional user@
        path = _SCHEME_USER_RE.sub("", url)
        # Strip hostname (everything up to the first /)
        slash = path.find("/")
        if slash == -1:
            return None
        path = path[slash + 1 :]

    # Normalize: lowercase, strip leading slash, strip trailing .git
    path = path.strip("/").lower()
    if path.endswith(".git"):
        path = path[:-4]

    # Must contain at least one slash (owner/repo, or deeper group/.../repo)
    if not path or "/" not in path:
        return None

    return path


def resolve_project_id(
    repo_path: str,
    remote_url: str | None,
    project_config: dict,
) -> str:
    """Derive a stable 16-char repo_id.

    Precedence:
    1. MEMEMO_REPO_ID env var
    2. project_config['project_id'] (from .mememo/project.yaml)
    3. hash of normalize_remote(remote_url) when URL is present
    4. hash_path(repo_path) fallback

    The returned id always has the same 16-char shape as hash_path outputs.
    """
    from ..utils.hashing import hash_path

    env_id = os.environ.get("MEMEMO_REPO_ID", "").strip()
    if env_id:
        return env_id[:16].ljust(16, "0") if len(env_id) < 16 else env_id[:16]

    config_id = project_config.get("project_id", "")
    if config_id and isinstance(config_id, str):
        cid = config_id.strip()
        if cid:
            return cid[:16].ljust(16, "0") if len(cid) < 16 else cid[:16]

    normalized = normalize_remote(remote_url)
    if normalized:
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    return hash_path(repo_path)
