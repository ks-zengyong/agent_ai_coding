"""Tests for TUI formatting helpers."""
from __future__ import annotations

from src.tui_format import (
    format_diff_result,
    format_tool_args,
    format_usage_brief,
    looks_like_markdown,
    thinking_preview,
)


def test_format_tool_args_truncates_long_values():
    args = {"path": "a" * 80}
    result = format_tool_args(args)
    assert result.endswith("...")
    assert len(result) <= 63


def test_format_tool_args_empty():
    assert format_tool_args({}) == ""


def test_format_diff_result_write_file():
    result = format_diff_result("write_file", {"path": "x.py", "content": "a\nb\nc"})
    assert result == "✓ Created: x.py (+3 lines)"


def test_format_diff_result_edit_file():
    result = format_diff_result(
        "edit_file",
        {"path": "x.py", "old_text": "a", "new_text": "a\nb"},
    )
    assert "✓ Edited: x.py" in result


def test_format_diff_result_other_tool():
    assert format_diff_result("read_file", {"path": "x"}) is None


def test_looks_like_markdown_detects_code_fence():
    assert looks_like_markdown("Here is code:\n```python\nprint(1)\n```")


def test_looks_like_markdown_short_text():
    assert not looks_like_markdown("hello")


def test_thinking_preview_truncates():
    text = "line one\nline two\nline three\nline four"
    preview = thinking_preview(text, max_lines=2)
    assert "line one" in preview
    assert "…" in preview


def test_format_usage_brief():
    assert "total tokens=100" in format_usage_brief({"total_tokens": 100})
