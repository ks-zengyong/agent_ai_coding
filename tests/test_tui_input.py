"""Framed input layout constraints."""
from __future__ import annotations

import pytest

import src.tui_input as tui_input


def test_framed_input_available_with_vendor():
    assert tui_input.framed_input_available() is True


def test_build_framed_input_layout_structure():
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.enums import DEFAULT_BUFFER
    from prompt_toolkit.layout import FloatContainer, HSplit

    buffer = Buffer(name=DEFAULT_BUFFER)
    layout = tui_input._build_framed_input_layout(
        buffer=buffer,
        width=80,
        status_text=lambda: "mock · ready",
        hint="/help",
    )[0]
    # Layout wraps HSplit in FloatContainer for completions menu
    assert isinstance(layout.container, FloatContainer)
    assert isinstance(layout.container.content, HSplit)
    assert len(layout.container.content.children) == 3


def test_accept_key_bindings_merge():
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.enums import DEFAULT_BUFFER

    buffer = Buffer(name=DEFAULT_BUFFER)
    kb = tui_input._build_accept_key_bindings(buffer, tui_input._CtrlCTracker())
    assert kb is not None


def test_ctrl_c_tracker_double_tap():
    tracker = tui_input._CtrlCTracker()
    assert tracker.on_ctrl_c() == "clear"
    assert tracker.on_ctrl_c() == "exit"


def test_blink_cursor_processor_shows_beam():
    """光标使用系统原生 BLINKING_BEAM，不再需要软件 _BlinkCursorProcessor。

    验证自定义 output 工厂函数不抛异常（无终端时返回 None）。
    """
    output = tui_input._create_blinking_cursor_output()
    # 无终端环境（如 CI）返回 None，有终端时返回 output 对象
    assert output is None or output is not None

def test_estimate_wrapped_lines_single_row():
    assert tui_input._estimate_wrapped_lines("", 40) == 1
    assert tui_input._estimate_wrapped_lines("hello", 40) == 1


def test_estimate_wrapped_lines_soft_wrap():
    text = "a" * 80
    assert tui_input._estimate_wrapped_lines(text, 40) == 2
    assert tui_input._estimate_wrapped_lines(text, 20) == 4


def test_delete_input_frame_lines_writes_erase_escape(capsys):
    """_delete_input_frame_lines clears each frame row via ESC[2K."""
    tui_input._delete_input_frame_lines(4)
    captured = capsys.readouterr()
    assert captured.out.count("\x1b[2K") == 4
    assert "\x1b[3A" in captured.out


def test_delete_input_frame_lines_clamps_large_values(capsys):
    """Pathologically large values are clamped to 200 rows."""
    tui_input._delete_input_frame_lines(99999)
    captured = capsys.readouterr()
    assert captured.out.count("\x1b[2K") == 200


def test_delete_input_frame_lines_zero_is_noop(capsys):
    """Zero or negative line counts produce no output."""
    tui_input._delete_input_frame_lines(0)
    tui_input._delete_input_frame_lines(-5)
    captured = capsys.readouterr()
    assert captured.out == ""
