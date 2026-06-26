"""Tests for shell, toolchain, and agent empty-response handling."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agent import AgentState, CodingAgent
from src.llm.base import ToolCall, ChatResponse
from src.llm.mock_provider import MockProvider
from src.permissions import PermissionGuard, PermissionType
from src.tools.registry import ToolRegistry, Tool, ToolResult
from src.tools.shell import format_command_output, DetectBuildToolchainTool
from src.context import MessageType


class CountingTool(Tool):
    name: str = "counting_tool"
    description: str = "counts"
    parameters: dict = {"type": "object", "properties": {}, "required": []}
    category: str = "readonly"

    def __init__(self):
        self.calls = 0

    async def execute(self, **kwargs) -> ToolResult:
        self.calls += 1
        return ToolResult(success=True, output=f"call_{self.calls}")


class SequenceProvider(MockProvider):
    def __init__(self, sequence):
        super().__init__(responses=["unused"])
        self._sequence = sequence
        self._idx = 0

    async def chat(self, messages):
        item = self._sequence[min(self._idx, len(self._sequence) - 1)]
        self._idx += 1
        if isinstance(item, Exception):
            raise item
        return item


def test_format_command_output_empty_stdout():
    text = format_command_output("", "", 0)
    assert "exit_code: 0" in text
    assert "stdout: (empty)" in text


def test_format_command_output_failed_no_output():
    text = format_command_output("", "", 1)
    assert "exit_code: 1" in text
    assert "hint:" in text


def test_format_command_output_with_stderr():
    text = format_command_output("", "not found", 1)
    assert "stderr: not found" in text


@pytest.mark.asyncio
async def test_agent_executes_all_queued_tools():
    t = CountingTool()
    registry = ToolRegistry()
    registry.register(t)
    guard = PermissionGuard()
    guard.register_tool("counting_tool", PermissionType.READ_ONLY)

    provider = SequenceProvider([
        ChatResponse(
            content="run both",
            tool_calls=[
                ToolCall(id="1", name="counting_tool", arguments={}),
                ToolCall(id="2", name="counting_tool", arguments={}),
            ],
        ),
        ChatResponse(content="done"),
    ])
    provider.set_tool_definitions(registry.get_tool_definitions())

    agent = CodingAgent(provider, registry, guard, max_loop=20)
    agent.session.add_message(
        __import__("src.context", fromlist=["Message"]).Message(
            type=MessageType.USER, content="go"
        )
    )
    agent.state = AgentState.IDLE

    for _ in range(10):
        if agent.state in (AgentState.DONE, AgentState.ERROR):
            break
        await agent.step()

    assert t.calls == 2
    tool_results = [m for m in agent.session.messages if m.type == MessageType.TOOL_RESULT]
    assert len(tool_results) == 2


@pytest.mark.asyncio
async def test_agent_retries_on_empty_response():
    registry = ToolRegistry()
    guard = PermissionGuard()
    provider = SequenceProvider([
        ChatResponse(content=""),
        ChatResponse(
            content="Task completed. Build succeeded.",
            tool_calls=None,
        ),
    ])
    provider.set_tool_definitions([])

    agent = CodingAgent(provider, registry, guard, max_loop=10)
    agent.session.add_message(
        __import__("src.context", fromlist=["Message"]).Message(
            type=MessageType.USER, content="build cpp"
        )
    )
    # Simulate prior tool work so completion text is accepted
    agent.session.add_message(
        __import__("src.context", fromlist=["Message"]).Message(
            type=MessageType.TOOL_RESULT, content="ok", tool_call_id="1"
        )
    )
    agent.state = AgentState.IDLE

    for _ in range(8):
        if agent.state == AgentState.DONE:
            break
        await agent.step()

    assert agent.state == AgentState.DONE
    assert provider._idx >= 2


@pytest.mark.asyncio
async def test_agent_nudges_text_only_plan_without_tools():
    registry = ToolRegistry()
    guard = PermissionGuard()
    provider = SequenceProvider([
        ChatResponse(
            content="I'll start by exploring the workspace to find snake code.",
        ),
        ChatResponse(
            content="",
            tool_calls=[ToolCall(id="1", name="glob", arguments={"pattern": "**/*snake*"})],
        ),
        ChatResponse(content="Task completed. Compilation successful."),
    ])
    registry.register(__import__("src.tools.filesystem", fromlist=["GlobTool"]).GlobTool())
    provider.set_tool_definitions(registry.get_tool_definitions())

    agent = CodingAgent(provider, registry, guard, max_loop=20)
    agent.session.add_message(
        __import__("src.context", fromlist=["Message"]).Message(
            type=MessageType.USER, content="编译snake_name"
        )
    )
    agent.state = AgentState.IDLE

    for _ in range(15):
        if agent.state == AgentState.DONE:
            break
        await agent.step()

    assert any(
        "plan" in m.content.lower() or "tool" in m.content.lower()
        for m in agent.session.messages
        if m.type == MessageType.USER and m.content.startswith("[System]")
    )
    tool_results = [m for m in agent.session.messages if m.type == MessageType.TOOL_RESULT]
    assert len(tool_results) >= 1


@pytest.mark.asyncio
async def test_detect_build_toolchain_tool():
    tool = DetectBuildToolchainTool()
    result = await tool.execute()
    assert result.success
    assert "platform:" in result.output
