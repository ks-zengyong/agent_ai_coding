"""Tests for echoed user messages and turn spacing."""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio

from src.agent import AgentState, CodingAgent
from src.llm.base import StreamEvent, StreamEventType
from src.llm.mock_provider import MockProvider
from src.permissions import PermissionGuard
from src.tools.registry import ToolRegistry
import src.tui as tui_mod
from src.tui import TUICodingAgent, _collapse_blank_lines


class _SimpleMock(MockProvider):
    async def chat_stream_events(self, messages):
        yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="Assistant reply.")
        yield StreamEvent(type=StreamEventType.DONE)


def test_print_user_message_echoes_submitted_text():
    guard = PermissionGuard()
    agent = CodingAgent(
        provider=_SimpleMock(responses=["ok"]),
        tool_registry=ToolRegistry(),
        permission_guard=guard,
    )
    buf = io.StringIO()
    from rich.console import Console

    captured = Console(file=buf, force_terminal=False, width=100)
    original = tui_mod.console
    tui_mod.console = captured

    tui = TUICodingAgent(agent, guard)
    tui.print_user_message("用 C++ 实现贪吃蛇")

    tui_mod.console = original
    rendered = buf.getvalue()
    assert "用 C++ 实现贪吃蛇" in rendered
    assert "> " in rendered
    assert "─" in rendered


def test_run_interactive_turn_includes_user_and_assistant():
    guard = PermissionGuard()
    agent = CodingAgent(
        provider=_SimpleMock(responses=["Assistant reply."]),
        tool_registry=ToolRegistry(),
        permission_guard=guard,
    )
    buf = io.StringIO()
    from rich.console import Console

    captured = Console(file=buf, force_terminal=False, width=100, record=True)
    original = tui_mod.console
    tui_mod.console = captured

    tui = TUICodingAgent(agent, guard)

    async def _run():
        tui.print_user_message("First task")
        await tui.run_agent_loop("First task")

    try:
        asyncio.get_event_loop().run_until_complete(_run())
    finally:
        tui_mod.console = original

    rendered = captured.export_text()
    assert "First task" in rendered
    assert "Assistant reply." in rendered
    assert "\n\n\n" not in rendered


def test_collapse_blank_lines_still_used_for_user_message():
    lines = ["", "", "hello", "", ""]
    assert _collapse_blank_lines(lines) == ["", "hello"]
