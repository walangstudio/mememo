"""Tests for response compressor."""

from mememo.context.response_compressor import ResponseCompressor


def test_empty_input():
    rc = ResponseCompressor()
    assert rc.preprocess("") == ""


def test_collapse_whitespace():
    rc = ResponseCompressor()
    result = rc.preprocess("line1\n\n\n\n\nline2")
    assert result == "line1\n\nline2"


def test_strip_progress_bars():
    rc = ResponseCompressor()
    result = rc.preprocess("Loading ███████░░░ 80%\nDone")
    assert "███" not in result
    assert "Done" in result


def test_strip_mememo_inject_block():
    rc = ResponseCompressor()
    text = "user: hello\n\nRelevant memories from previous sessions:\n- [context] some memory\n- [decision] another\n\nassistant: hi"
    result = rc.preprocess(text)
    assert "Relevant memories" not in result
    assert "user: hello" in result
    assert "assistant: hi" in result


def test_strip_system_reminder_tags():
    rc = ResponseCompressor()
    text = "before <system-reminder>secret stuff here</system-reminder> after"
    result = rc.preprocess(text)
    assert "<system-reminder>" not in result
    assert "before" in result
    assert "after" in result


def test_truncate_long_code_blocks():
    rc = ResponseCompressor()
    lines = "\n".join(f"line {i}" for i in range(50))
    text = f"before\n```python\n{lines}\n```\nafter"
    result = rc.preprocess(text)
    assert "lines omitted" in result
    assert "line 0" in result
    assert "line 49" in result
    assert "line 25" not in result


def test_short_code_blocks_preserved():
    rc = ResponseCompressor()
    text = "```python\ndef foo():\n    return 42\n```"
    result = rc.preprocess(text)
    assert "def foo():" in result
    assert "omitted" not in result


def test_build_enhanced_prompt_empty():
    result = ResponseCompressor.build_enhanced_prompt("base prompt", [])
    assert result == "base prompt"


def test_build_enhanced_prompt_with_summaries():
    result = ResponseCompressor.build_enhanced_prompt("base prompt", [
        "[decision] Use SQLite",
        "[context] Python project",
    ])
    assert "Already stored" in result
    assert "Use SQLite" in result
    assert "Python project" in result
    assert "base prompt" in result


def test_build_enhanced_prompt_limits_to_20():
    summaries = [f"summary {i}" for i in range(30)]
    result = ResponseCompressor.build_enhanced_prompt("base", summaries)
    assert "summary 19" in result
    assert "summary 20" not in result


def test_tool_blocks_stripped():
    rc = ResponseCompressor()
    text = "before <tool_use>some tool call data</tool_use> after"
    result = rc.preprocess(text)
    assert "<tool_use>" not in result
    assert "before" in result
    assert "after" in result
