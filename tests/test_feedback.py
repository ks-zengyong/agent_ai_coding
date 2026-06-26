"""Tests for debug_info, feedback, and agent_runner."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src import debug_info
from src.agent import AgentState
from src.agent_runner import run_agent_until_done
from src.context import Message, MessageType
from src.feedback import validate_snake_python
from src.llm.base import ToolCall
from src.llm.mock_provider import MockProvider
from src.permissions import PermissionGuard
from src.tools import ToolRegistry, ReadFileTool
from src.agent import CodingAgent


def test_debug_info_no_crash(capsys):
    debug_info.configure(enabled=True, log_level="debug")
    debug_info.log_request(
        provider="openai",
        model="test",
        api_url="http://localhost",
        api_key="secretkey12345",
        messages=[],
    )
    debug_info.log_response(content="hello")
    debug_info.log_error("test error", suggestion="retry")
    debug_info.log_info("info message")


def test_validate_snake_python_success():
    root = Path(__file__).resolve().parents[1]
    snake = root / "deliverables" / "snake_demo" / "snake.py"
    result = validate_snake_python(snake)
    assert result.success, result.summary()


def test_validate_snake_python_missing(tmp_path):
    result = validate_snake_python(tmp_path / "missing.py")
    assert not result.success
    assert result.issues[0].category == "missing_file"


@pytest.mark.asyncio
async def test_agent_runner_auto_approve():
    provider = MockProvider(
        responses=["Reading file", "Done"],
        tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "x"})],
    )
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    provider.set_tool_definitions(registry.get_tool_definitions())
    agent = CodingAgent(provider, registry, PermissionGuard(), max_loop=10)

    state = await run_agent_until_done(agent, "read x", auto_approve=True, max_steps=20)
    assert state in (AgentState.DONE, AgentState.THINKING, AgentState.EXECUTING)
    assert any(m.type == MessageType.USER for m in agent.session.messages)
