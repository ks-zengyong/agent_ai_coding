"""Framed input layout constraints."""
from __future__ import annotations

import pytest

import src.tui_input as tui_input


def test_framed_input_available_with_vendor():
    assert tui_input.framed_input_available() is True


def test_build_framed_input_layout_structure():
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.enums import DEFAULT_BUFFER
    from prompt_toolkit.layout import HSplit

    buffer = Buffer(name=DEFAULT_BUFFER)
    layout = tui_input._build_framed_input_layout(
        buffer=buffer,
        width=80,
        status_text=lambda: "mock · ready",
        hint="/help",
    )[0]
    assert isinstance(layout.container, HSplit)
    assert len(layout.container.children) == 3


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
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.document import Document
    from prompt_toolkit.layout.controls import BufferControl
    from prompt_toolkit.layout.processors import TransformationInput

    proc = tui_input._BlinkCursorProcessor()
    buffer = Buffer()
    buffer.text = "hi"
    buffer.cursor_position = 2
    control = BufferControl(buffer=buffer)
    ti = TransformationInput(
        control,
        Document("hi", 2),
        0,
        lambda i: i,
        [("", "hi")],
        80,
        1,
    )

    class _FakeApp:
        is_done = False
        render_counter = 0

    import prompt_toolkit.application.current as current

    old = current.get_app
    current.get_app = lambda: _FakeApp()
    try:
        out = proc.apply_transformation(ti)
    finally:
        current.get_app = old

    assert any(
        tui_input._CURSOR_BEAM in (frag[1] or "")
        or "cursor-blink" in (frag[0] or "")
        for frag in out.fragments
    )

def test_estimate_wrapped_lines_single_row():
    assert tui_input._estimate_wrapped_lines("", 40) == 1
    assert tui_input._estimate_wrapped_lines("hello", 40) == 1


def test_estimate_wrapped_lines_soft_wrap():
    text = "a" * 80
    assert tui_input._estimate_wrapped_lines(text, 40) == 2
    assert tui_input._estimate_wrapped_lines(text, 20) == 4
