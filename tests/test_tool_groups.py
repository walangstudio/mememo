"""Tool-group gating: MEMEMO_TOOLS / MEMEMO_DISABLE_TOOLS shrink the MCP surface."""

from __future__ import annotations

import pytest

from mememo.tool_groups import (
    ALL_GROUPS,
    TOOL_GROUPS,
    apply_tool_filter,
    disabled_tool_names,
    enabled_groups,
)


def test_default_exposes_all_groups():
    assert enabled_groups({}) == set(ALL_GROUPS)


def test_allowlist_limits_to_named_groups():
    assert enabled_groups({"MEMEMO_TOOLS": "core, comprehension"}) == {"core", "comprehension"}


def test_denylist_subtracts():
    assert enabled_groups({"MEMEMO_DISABLE_TOOLS": "skills,diagrams"}) == set(ALL_GROUPS) - {
        "skills",
        "diagrams",
    }


def test_allowlist_then_denylist():
    got = enabled_groups({"MEMEMO_TOOLS": "core,skills", "MEMEMO_DISABLE_TOOLS": "skills"})
    assert got == {"core"}


def test_contradictory_allow_and_deny_keeps_allowlist_not_all():
    # Allowing then denying the same group must not blow the surface open to ALL;
    # it falls back to the permitted base (the allowlist).
    got = enabled_groups({"MEMEMO_TOOLS": "core", "MEMEMO_DISABLE_TOOLS": "core"})
    assert got == {"core"}


def test_typo_allowlist_falls_back_to_all():
    # An allowlist with no valid group must not leave the server with no tools.
    assert enabled_groups({"MEMEMO_TOOLS": "bogus"}) == set(ALL_GROUPS)


def test_disabling_every_group_falls_back_to_all():
    assert enabled_groups({"MEMEMO_DISABLE_TOOLS": ",".join(ALL_GROUPS)}) == set(ALL_GROUPS)


def test_disabled_tool_names_are_exactly_the_other_groups():
    names = set(disabled_tool_names({"MEMEMO_TOOLS": "core"}))
    assert "ask" in names and "manage_skill" in names  # comprehension + skills dropped
    assert "store_memory" not in names  # core kept
    assert all(TOOL_GROUPS[n] != "core" for n in names)


class _Provider:
    def __init__(self):
        self.removed: list[str] = []

    def remove_tool(self, name):
        self.removed.append(name)


class _Mcp:
    def __init__(self):
        self.local_provider = _Provider()


def test_apply_filter_removes_exactly_the_disabled_tools():
    m = _Mcp()
    removed = apply_tool_filter(m, {"MEMEMO_DISABLE_TOOLS": "skills"})
    assert set(removed) == {"manage_skill", "curate_skills"}
    assert set(m.local_provider.removed) == {"manage_skill", "curate_skills"}


def test_apply_filter_is_noop_when_all_enabled():
    m = _Mcp()
    assert apply_tool_filter(m, {}) == []
    assert m.local_provider.removed == []


def test_apply_filter_skips_when_no_remove_api():
    # A FastMCP exposing neither remover must degrade gracefully, not crash startup.
    class _Bare:
        pass

    assert apply_tool_filter(_Bare(), {"MEMEMO_DISABLE_TOOLS": "skills"}) == []


@pytest.mark.asyncio
async def test_no_drift_between_map_and_registered_tools():
    # Every @mcp.tool() must have a group, and every mapped name must be a real
    # tool — otherwise the switch would silently miss a tool or fail to remove it.
    import mememo.server as s

    registered = {t.name for t in await s.mcp._list_tools()}
    assert set(TOOL_GROUPS) == registered
