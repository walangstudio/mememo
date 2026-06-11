"""The git subprocess hardening that prevents the MCP-server init hang.

mememo runs git from inside the long-lived MCP server, whose stdin is Claude
Code's stdio pipe. If a git invocation inherits that stdin, or spawns a pager /
credential / fsmonitor helper that outlives `git` and holds the captured stdout
pipe, subprocess.communicate() blocks forever and the timeout never fires — the
exact deadlock that froze first-call init. These tests pin the mitigations.
"""

from __future__ import annotations

import subprocess

import pytest

from mememo.core.git_manager import _GIT_SAFE_ENV, _GIT_SAFE_FLAGS, GitManager


@pytest.fixture
def captured_run(monkeypatch):
    calls = {}

    def fake_run(argv, **kwargs):
        calls["argv"] = argv
        calls["kwargs"] = kwargs

        class _R:
            returncode = 0
            stdout = "/repo/root\n"
            stderr = ""

        return _R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


async def test_exec_git_detaches_stdin_and_hardens_env(captured_run):
    gm = GitManager()
    out = await gm._exec_git("rev-parse", ["--show-toplevel"], cwd="/repo")
    assert out == "/repo/root"

    kw = captured_run["kwargs"]
    # stdin must be detached from the MCP pipe so git can't block reading it.
    assert kw["stdin"] is subprocess.DEVNULL
    # the hardened env must be applied (and merged, not replaced).
    for key, val in _GIT_SAFE_ENV.items():
        assert kw["env"].get(key) == val
    assert kw["env"].get("GIT_TERMINAL_PROMPT") == "0"
    # timeout preserved so a genuinely slow git still can't hang init.
    assert kw["timeout"] == 30


async def test_exec_git_injects_daemon_suppressing_flags(captured_run):
    gm = GitManager()
    await gm._exec_git("status", ["--porcelain"], cwd="/repo")

    argv = captured_run["argv"]
    assert argv[0] == "git"
    # the -c overrides must come before the subcommand.
    head = argv[: 1 + len(_GIT_SAFE_FLAGS)]
    assert head == ["git", *_GIT_SAFE_FLAGS]
    assert argv[1 + len(_GIT_SAFE_FLAGS)] == "status"
    # both fsmonitor and credential helper are neutralised.
    assert "core.fsmonitor=" in _GIT_SAFE_FLAGS
    assert "credential.helper=" in _GIT_SAFE_FLAGS


async def test_exec_git_config_skips_shadowing_flags_but_keeps_env(captured_run):
    """`config` must NOT get the -c overrides (they'd shadow a config --get of
    core.fsmonitor/credential.helper) but still gets stdin/env hardening."""
    gm = GitManager()
    await gm._exec_git("config", ["--get", "remote.origin.url"], cwd="/repo")

    argv = captured_run["argv"]
    assert argv[:2] == ["git", "config"]  # no -c flags injected before the subcommand
    assert "-c" not in argv
    # hardening that doesn't shadow config values still applies.
    assert captured_run["kwargs"]["stdin"] is subprocess.DEVNULL
    assert captured_run["kwargs"]["env"].get("GIT_TERMINAL_PROMPT") == "0"


async def test_exec_git_rejects_non_whitelisted_command(captured_run):
    gm = GitManager()
    with pytest.raises(ValueError, match="not allowed"):
        await gm._exec_git("push", [], cwd="/repo")  # type: ignore[arg-type]
    assert "argv" not in captured_run  # never reached subprocess
