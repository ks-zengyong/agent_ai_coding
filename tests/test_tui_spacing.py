"""Tests for TUI blank-line / spacing helpers in src.tui."""
from __future__ import annotations

from src.tui import _collapse_blank_lines, _needs_blank_separator, _strip_trailing_newlines


def test_collapse_blank_lines_collapses_consecutive_blanks():
    lines = ["a", "", "", "b", "", "", "", "c"]
    result = _collapse_blank_lines(lines)
    assert result == ["a", "", "b", "", "c"]


def test_collapse_blank_lines_strips_trailing_blanks():
    lines = ["a", "b", "", "", ""]
    result = _collapse_blank_lines(lines)
    assert result == ["a", "b"]


def test_collapse_blank_lines_collapses_leading_blanks_to_one():
    # Leading blanks collapse to a single blank (not fully stripped, since
    # the function only guarantees collapsing consecutive blanks + trailing).
    lines = ["", "", "a", "b"]
    result = _collapse_blank_lines(lines)
    assert result == ["", "a", "b"]


def test_collapse_blank_lines_all_blank():
    assert _collapse_blank_lines(["", "", ""]) == []


def test_collapse_blank_lines_no_blanks():
    assert _collapse_blank_lines(["a", "b", "c"]) == ["a", "b", "c"]


def test_collapse_blank_lines_empty():
    assert _collapse_blank_lines([]) == []


def test_collapse_blank_lines_single_blank():
    assert _collapse_blank_lines([""]) == []


def test_collapse_blank_lines_preserves_single_blank():
    lines = ["a", "", "b"]
    assert _collapse_blank_lines(lines) == ["a", "", "b"]


def test_strip_trailing_newlines():
    assert _strip_trailing_newlines("text\n\n\n") == "text"
    assert _strip_trailing_newlines("text\r\n") == "text"
    assert _strip_trailing_newlines("text   \n  ") == "text"
    assert _strip_trailing_newlines("text") == "text"
    assert _strip_trailing_newlines("") == ""


def test_needs_blank_separator_both_present_no_trailing_newline():
    assert _needs_blank_separator("text", "text2") is True


def test_needs_blank_separator_previous_ends_with_newline():
    assert _needs_blank_separator("text\n", "text2") is False


def test_needs_blank_separator_empty_before():
    assert _needs_blank_separator("", "text2") is False


def test_needs_blank_separator_empty_after():
    assert _needs_blank_separator("text", "") is False
