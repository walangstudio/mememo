"""Tests for mememo.core.identity — resolver, normalize_remote, GLOBAL_REPO_ID."""

import hashlib

from mememo.core.identity import GLOBAL_REPO_ID, normalize_remote, resolve_project_id
from mememo.utils.hashing import hash_path

# ---------------------------------------------------------------------------
# normalize_remote
# ---------------------------------------------------------------------------


class TestNormalizeRemote:
    def test_none_returns_none(self):
        assert normalize_remote(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_remote("") is None

    def test_https_strips_scheme_host_git(self):
        assert normalize_remote("https://github.com/owner/repo.git") == "owner/repo"

    def test_https_no_git_suffix(self):
        assert normalize_remote("https://github.com/owner/repo") == "owner/repo"

    def test_scp_style(self):
        assert normalize_remote("git@github.com:owner/repo.git") == "owner/repo"

    def test_scp_no_git_suffix(self):
        assert normalize_remote("git@github.com:owner/repo") == "owner/repo"

    def test_scp_alias_gh_kitty(self):
        """gh-kitty SSH alias normalizes to the same path as github.com."""
        scp = normalize_remote("git@gh-kitty:owner/repo.git")
        https = normalize_remote("https://github.com/owner/repo.git")
        # Both produce 'owner/repo'; alias resolution strips to path only
        assert scp == https

    def test_lowercase(self):
        assert normalize_remote("https://github.com/Owner/Repo.git") == "owner/repo"

    def test_ssh_scheme(self):
        assert normalize_remote("ssh://git@github.com/owner/repo.git") == "owner/repo"

    def test_git_scheme(self):
        assert normalize_remote("git://github.com/owner/repo.git") == "owner/repo"

    def test_ssh_scheme_with_port_not_misread_as_scp(self):
        # Regression: ssh://user@host:port/path must NOT match the SCP branch
        # (which would make the port the "owner"). Should resolve owner/repo.
        assert normalize_remote("ssh://git@github.com:22/owner/repo.git") == "owner/repo"

    def test_ssh_scheme_with_port_equals_https(self):
        assert normalize_remote("ssh://git@github.com:2222/owner/repo.git") == normalize_remote(
            "https://github.com/owner/repo.git"
        )

    def test_trailing_slash(self):
        assert normalize_remote("https://github.com/owner/repo/") == "owner/repo"

    def test_no_owner_returns_none(self):
        # Just a hostname — no owner/repo shape
        assert normalize_remote("https://github.com/") is None

    def test_scp_returns_none_without_slash_in_path(self):
        # Unusual: no slash in the path portion
        assert normalize_remote("git@github.com:nodirrepo") is None


# ---------------------------------------------------------------------------
# resolve_project_id — four precedence levels
# ---------------------------------------------------------------------------


class TestResolveProjectId:
    def test_env_var_takes_precedence(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEMEMO_REPO_ID", "myexplicitid1234")
        result = resolve_project_id(
            str(tmp_path),
            "https://github.com/owner/repo.git",
            {"project_id": "config_id_xyz"},
        )
        assert result == "myexplicitid1234"

    def test_env_var_truncated_to_16(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MEMEMO_REPO_ID", "a" * 32)
        result = resolve_project_id(str(tmp_path), None, {})
        assert len(result) == 16

    def test_project_config_second(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MEMEMO_REPO_ID", raising=False)
        result = resolve_project_id(
            str(tmp_path),
            "https://github.com/owner/repo.git",
            {"project_id": "myprojectid12345"},
        )
        assert result == "myprojectid12345"

    def test_project_config_short_padded(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MEMEMO_REPO_ID", raising=False)
        result = resolve_project_id(str(tmp_path), None, {"project_id": "short"})
        assert len(result) == 16
        assert result.startswith("short")

    def test_remote_url_third(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MEMEMO_REPO_ID", raising=False)
        remote = "https://github.com/owner/myrepo.git"
        result = resolve_project_id(str(tmp_path), remote, {})
        expected = hashlib.sha256(b"owner/myrepo").hexdigest()[:16]
        assert result == expected

    def test_remote_url_scp_equals_https(self, monkeypatch, tmp_path):
        """SCP and HTTPS remotes for the same repo produce the same id."""
        monkeypatch.delenv("MEMEMO_REPO_ID", raising=False)
        r_https = resolve_project_id(str(tmp_path), "https://github.com/owner/repo.git", {})
        r_scp = resolve_project_id(str(tmp_path), "git@github.com:owner/repo.git", {})
        assert r_https == r_scp

    def test_gh_kitty_alias_equals_github(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MEMEMO_REPO_ID", raising=False)
        r_alias = resolve_project_id(str(tmp_path), "git@gh-kitty:owner/repo.git", {})
        r_canonical = resolve_project_id(str(tmp_path), "https://github.com/owner/repo.git", {})
        assert r_alias == r_canonical

    def test_path_fallback_no_remote(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MEMEMO_REPO_ID", raising=False)
        result = resolve_project_id(str(tmp_path), None, {})
        assert result == hash_path(str(tmp_path))

    def test_path_fallback_deterministic(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MEMEMO_REPO_ID", raising=False)
        r1 = resolve_project_id(str(tmp_path), None, {})
        r2 = resolve_project_id(str(tmp_path), None, {})
        assert r1 == r2

    def test_result_always_16_chars(self, monkeypatch, tmp_path):
        monkeypatch.delenv("MEMEMO_REPO_ID", raising=False)
        result = resolve_project_id(str(tmp_path), None, {})
        assert len(result) == 16


# ---------------------------------------------------------------------------
# GLOBAL_REPO_ID
# ---------------------------------------------------------------------------


class TestGlobalRepoId:
    def test_length_16(self):
        assert len(GLOBAL_REPO_ID) == 16

    def test_is_hex(self):
        int(GLOBAL_REPO_ID, 16)  # raises ValueError if not hex

    def test_stable(self):
        expected = hashlib.sha256(b"::global::").hexdigest()[:16]
        assert GLOBAL_REPO_ID == expected
