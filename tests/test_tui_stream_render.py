"""Tests for streamed text rendering: blank-line collapsing and leading-space trimming.

These tests verify the _flush_stream_buffer logic inside
TUICodingAgent._run_thinking_stream by exercising it through a captured
console. We simulate TEXT_DELTA events that contain consecutive blank lines
and lines with heavy leading whitespace, then assert the rendered output
contains no stacked blanks and no leading-space lines.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.agent import AgentState, CodingAgent
from src.llm.base import StreamEvent, StreamEventType
from src.llm.mock_provider import MockProvider
from src.permissions import PermissionGuard
from src.tools.registry import ToolRegistry


def _make_agent(provider, registry):
    guard = PermissionGuard()
    return CodingAgent(provider=provider, tool_registry=registry, permission_guard=guard)


def _capture_tui_stream(provider, registry, config=None):
    """Run _run_thinking_stream with a captured stdout and return rendered text."""
    import src.tui as tui_mod

    agent = _make_agent(provider, registry)
    agent.state = AgentState.THINKING

    # Replace the module-level console with one writing to a StringIO so we
    # can inspect what was actually rendered.
    from rich.console import Console
    buf = io.StringIO()
    captured_console = Console(file=buf, force_terminal=False, width=100, record=True)
    original_console = tui_mod.console
    tui_mod.console = captured_console

    tui = tui_mod.TUICodingAgent(agent, PermissionGuard(), config=config)

    import asyncio

    async def _run():
        await tui._run_thinking_stream()

    try:
        asyncio.get_event_loop().run_until_complete(_run())
    finally:
        tui_mod.console = original_console

    return captured_console.export_text()


def test_stream_collapses_consecutive_blank_lines():
    """Multiple \\n in a row should render as at most one blank line."""

    class BlankHeavyMock(MockProvider):
        async def chat_stream_events(self, messages):
            # Text with 4 consecutive newlines in the middle and trailing blanks.
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="Line one\n\n\n\n\nLine two\n\n\n")
            yield StreamEvent(type=StreamEventType.DONE)

    provider = BlankHeavyMock(responses=["Line one\n\n\n\n\nLine two\n\n\n"])
    registry = ToolRegistry()

    rendered = _capture_tui_stream(provider, registry)

    # There should be no occurrence of 3+ consecutive newlines (i.e. 2+ blank
    # lines in a row) in the rendered output.
    assert "\n\n\n" not in rendered, (
        f"Consecutive blank lines not collapsed. Rendered:\n{rendered!r}"
    )
    # Both content lines should be present.
    assert "Line one" in rendered
    assert "Line two" in rendered


def test_stream_trims_leading_whitespace():
    """Lines with heavy leading whitespace should be trimmed."""

    class IndentedMock(MockProvider):
        async def chat_stream_events(self, messages):
            yield StreamEvent(
                type=StreamEventType.TEXT_DELTA,
                text="Header\n        deeply indented line\n    another indented\nNormal",
            )
            yield StreamEvent(type=StreamEventType.DONE)

    provider = IndentedMock(responses=["Header\n        deeply indented line\n    another indented\nNormal"])
    registry = ToolRegistry()

    rendered = _capture_tui_stream(provider, registry)

    lines = rendered.split("\n")
    # No rendered content line should start with more than 2 spaces.
    for line in lines:
        if line.strip() == "":
            continue
        leading = len(line) - len(line.lstrip(" "))
        assert leading <= 2, (
            f"Line has {leading} leading spaces (expected <=2): {line!r}"
        )
    assert "deeply indented line" in rendered
    assert "another indented" in rendered


def test_stream_preserves_single_blank_line():
    """A single blank line between content should be preserved."""

    class SingleBlankMock(MockProvider):
        async def chat_stream_events(self, messages):
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="First\n\nSecond")
            yield StreamEvent(type=StreamEventType.DONE)

    provider = SingleBlankMock(responses=["First\n\nSecond"])
    registry = ToolRegistry()

    rendered = _capture_tui_stream(provider, registry)

    assert "First" in rendered
    assert "Second" in rendered
    # Exactly one blank line between them (i.e. "First\n\nSecond" pattern).
    assert "First\n\nSecond" in rendered or "First\n \nSecond" in rendered


def test_stream_handles_incremental_deltas():
    """Text arriving in small chunks should still collapse blanks correctly."""

    class ChunkedMock(MockProvider):
        async def chat_stream_events(self, messages):
            full = "A\n\n\n\nB"
            for ch in full:
                yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=ch)
            yield StreamEvent(type=StreamEventType.DONE)

    provider = ChunkedMock(responses=["A\n\n\n\nB"])
    registry = ToolRegistry()

    rendered = _capture_tui_stream(provider, registry)

    assert "\n\n\n" not in rendered, (
        f"Consecutive blanks not collapsed for chunked input. Rendered:\n{rendered!r}"
    )
    assert "A" in rendered
    assert "B" in rendered
