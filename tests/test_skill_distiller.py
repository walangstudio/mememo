"""Unit tests for mememo.context.skill_distiller (Phase A self-learning loop)."""

from __future__ import annotations

import io
import json
from pathlib import Path

from mememo.context.skill_distiller import (
    VALID_INTENTS,
    build_distillation_reason,
    count_tool_uses,
    should_distill,
)


def _tool_lines(n: int) -> list[dict]:
    return [
        {"role": "assistant", "content": [{"type": "tool_use", "name": "Edit"}]} for _ in range(n)
    ]


def _run_distill_stdout(monkeypatch, capsys, hook_input: dict) -> dict:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_input)))
    from mememo.cli import run_distill

    run_distill()
    out_lines = [ln for ln in capsys.readouterr().out.strip().splitlines() if ln.strip()]
    return json.loads(out_lines[-1])


def _write_transcript(tmp_path: Path, lines: list[dict]) -> str:
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    return str(p)


def test_count_tool_uses_flat_and_nested(tmp_path: Path) -> None:
    path = _write_transcript(
        tmp_path,
        [
            {"role": "user", "content": "do the thing"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "ok"},
                    {"type": "tool_use", "name": "Edit"},
                    {"type": "tool_use", "name": "Bash"},
                ],
            },
            {"message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Read"}]}},
        ],
    )
    assert count_tool_uses(path, max_lines=100) == 3


def test_count_tool_uses_text_only_is_zero(tmp_path: Path) -> None:
    path = _write_transcript(
        tmp_path,
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        ],
    )
    assert count_tool_uses(path, max_lines=100) == 0


def test_count_tool_uses_respects_tail_window(tmp_path: Path) -> None:
    # Tool use only in the FIRST (oldest) line; a small tail window must miss it.
    path = _write_transcript(
        tmp_path,
        [
            {"role": "assistant", "content": [{"type": "tool_use", "name": "Edit"}]},
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
        ],
    )
    assert count_tool_uses(path, max_lines=2) == 0
    assert count_tool_uses(path, max_lines=100) == 1


def test_count_tool_uses_skips_malformed_and_missing(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    p.write_text('not json\n{"role":"user","content":"x"}\n', encoding="utf-8")
    assert count_tool_uses(str(p), max_lines=100) == 0
    assert count_tool_uses(str(tmp_path / "nope.jsonl"), max_lines=100) == 0


def test_should_distill_matrix() -> None:
    assert should_distill(enabled=True, stop_hook_active=False, num_tool_uses=5, min_tools=5)
    assert should_distill(enabled=True, stop_hook_active=False, num_tool_uses=9, min_tools=5)
    # below threshold
    assert not should_distill(enabled=True, stop_hook_active=False, num_tool_uses=4, min_tools=5)
    # disabled
    assert not should_distill(enabled=False, stop_hook_active=False, num_tool_uses=9, min_tools=5)
    # already continuing from a stop hook -> never re-distill (loop guard)
    assert not should_distill(enabled=True, stop_hook_active=True, num_tool_uses=9, min_tools=5)


def test_build_distillation_reason_contract() -> None:
    reason = build_distillation_reason(7)
    assert "manage_skill" in reason
    assert "action='create'" in reason
    assert "7 tools" in reason
    # every routable intent must be offered so the host picks an injectable one
    for intent in VALID_INTENTS:
        assert intent in reason


def test_run_distill_blocks_on_complex_session(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MEMEMO_HOOK_SKILL_DISTILL", "true")
    monkeypatch.setenv("MEMEMO_HOOK_SKILL_DISTILL_MIN_TOOLS", "3")
    path = _write_transcript(tmp_path, _tool_lines(4))
    out = _run_distill_stdout(
        monkeypatch, capsys, {"transcript_path": path, "stop_hook_active": False}
    )
    assert out.get("decision") == "block"
    assert "manage_skill" in out.get("reason", "")


def test_run_distill_continues_when_disabled(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MEMEMO_HOOK_SKILL_DISTILL", "false")
    path = _write_transcript(tmp_path, _tool_lines(9))
    out = _run_distill_stdout(
        monkeypatch, capsys, {"transcript_path": path, "stop_hook_active": False}
    )
    assert out == {"continue": True}


def test_run_distill_continues_below_threshold(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("MEMEMO_HOOK_SKILL_DISTILL", "true")
    monkeypatch.setenv("MEMEMO_HOOK_SKILL_DISTILL_MIN_TOOLS", "5")
    path = _write_transcript(tmp_path, _tool_lines(2))
    out = _run_distill_stdout(
        monkeypatch, capsys, {"transcript_path": path, "stop_hook_active": False}
    )
    assert out == {"continue": True}


def test_run_distill_no_block_when_stop_hook_active(tmp_path: Path, monkeypatch, capsys) -> None:
    # Loop guard: a stop already continuing from this hook must not re-distill.
    monkeypatch.setenv("MEMEMO_HOOK_SKILL_DISTILL", "true")
    monkeypatch.setenv("MEMEMO_HOOK_SKILL_DISTILL_MIN_TOOLS", "3")
    path = _write_transcript(tmp_path, _tool_lines(9))
    out = _run_distill_stdout(
        monkeypatch, capsys, {"transcript_path": path, "stop_hook_active": True}
    )
    assert out == {"continue": True}
