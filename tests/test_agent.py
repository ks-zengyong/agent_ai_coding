import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.agent import CodingAgent, AgentState
from src.llm.base import ToolCall, ChatMessage, ChatResponse
from src.llm.mock_provider import MockProvider
from src.tools.registry import ToolRegistry, Tool, ToolResult
from src.context import Message, MessageType, Session
from src.permissions import PermissionGuard, PermissionType


def _make_agent(provider, registry, max_loop=100):
    guard = PermissionGuard()
    guard.register_tool("readonly_tool", PermissionType.READ_ONLY)
    guard.register_tool("write_tool", PermissionType.WRITE)
    guard.register_tool("failing_tool", PermissionType.WRITE)
    return CodingAgent(
        provider=provider,
        tool_registry=registry,
        permission_guard=guard,
        max_loop=max_loop,
    )


class MockReadonlyTool(Tool):
    name: str = "readonly_tool"
    description: str = "A readonly test tool"
    parameters: dict = {"type": "object", "properties": {}, "required": []}
    category: str = "readonly"

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, output="readonly result")


class MockWriteTool(Tool):
    name: str = "write_tool"
    description: str = "A write test tool"
    parameters: dict = {"type": "object", "properties": {}, "required": []}
    category: str = "write"

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, output="write result")


def test_agent_initial_state():
    provider = MockProvider()
    registry = ToolRegistry()
    agent = _make_agent(provider=provider, registry=registry)
    assert agent.state == AgentState.IDLE
    assert agent.loop_count == 0
    assert agent.pending_tool_call is None
    assert len(agent.session.messages) == 0


async def test_think_no_tool_call_becomes_done():
    provider = MockProvider(responses=["Hello, world!"])
    registry = ToolRegistry()
    agent = _make_agent(provider=provider, registry=registry)

    await agent.think()

    assert agent.state == AgentState.DONE
    assert len(agent.session.messages) == 1
    assert agent.session.messages[0].type == MessageType.ASSISTANT
    assert agent.session.messages[0].content == "Hello, world!"
    assert provider.call_count == 1


async def test_think_readonly_tool_auto_executes():
    readonly_tool = MockReadonlyTool()
    tool_call = ToolCall(id="call_1", name="readonly_tool", arguments={})

    provider = MockProvider(
        responses=["First response", "Second response"],
        tool_calls=[tool_call],
    )
    registry = ToolRegistry()
    registry.register(readonly_tool)
    agent = _make_agent(provider=provider, registry=registry)

    await agent.think()

    assert agent.state == AgentState.EXECUTING
    assert agent.pending_tool_call is not None
    assert agent.pending_tool_call.name == "readonly_tool"
    assert len(agent.session.messages) == 1
    assert agent.session.messages[0].tool_calls is not None
    assert len(agent.session.messages[0].tool_calls) == 1


async def test_think_write_tool_awaits_permission():
    write_tool = MockWriteTool()
    tool_call = ToolCall(id="call_1", name="write_tool", arguments={})

    provider = MockProvider(
        responses=["Need to write"],
        tool_calls=[tool_call],
    )
    registry = ToolRegistry()
    registry.register(write_tool)
    agent = _make_agent(provider=provider, registry=registry)

    await agent.think()

    assert agent.state == AgentState.AWAITING_PERMISSION
    assert agent.pending_tool_call is not None
    assert agent.pending_tool_call.name == "write_tool"


async def test_think_tool_not_found_awaits_permission():
    tool_call = ToolCall(id="call_1", name="nonexistent_tool", arguments={})

    provider = MockProvider(
        responses=["Using unknown tool"],
        tool_calls=[tool_call],
    )
    registry = ToolRegistry()
    agent = _make_agent(provider=provider, registry=registry)

    await agent.think()

    assert agent.state == AgentState.AWAITING_PERMISSION
    assert agent.pending_tool_call is not None


async def test_handle_permission_denied():
    write_tool = MockWriteTool()
    tool_call = ToolCall(id="call_1", name="write_tool", arguments={})

    provider = MockProvider(
        responses=["Need to write"],
        tool_calls=[tool_call],
    )
    registry = ToolRegistry()
    registry.register(write_tool)
    agent = _make_agent(provider=provider, registry=registry)

    await agent.think()
    assert agent.state == AgentState.AWAITING_PERMISSION

    agent.handle_permission_denied(tool_call)

    assert agent.state == AgentState.THINKING
    assert agent.pending_tool_call is None
    assert len(agent.session.messages) == 2
    assert agent.session.messages[1].type == MessageType.PERMISSION_DENIED
    assert agent.session.messages[1].tool_call_id == "call_1"


async def test_think_with_exception_sets_error_state():
    provider = MockProvider()
    provider.chat = AsyncMock(side_effect=Exception("LLM error"))

    registry = ToolRegistry()
    agent = _make_agent(provider=provider, registry=registry)

    await agent.think()

    assert agent.state == AgentState.ERROR
    assert len(agent.session.messages) == 1
    assert agent.session.messages[0].type == MessageType.ERROR
    assert "LLM error" in agent.session.messages[0].content


async def test_execute_tool_success():
    readonly_tool = MockReadonlyTool()
    tool_call = ToolCall(id="call_1", name="readonly_tool", arguments={})

    provider = MockProvider()
    registry = ToolRegistry()
    registry.register(readonly_tool)
    agent = _make_agent(provider=provider, registry=registry)

    await agent.execute_tool(tool_call)

    assert len(agent.session.messages) == 1
    assert agent.session.messages[0].type == MessageType.TOOL_RESULT
    assert agent.session.messages[0].content == "readonly result"
    assert agent.session.messages[0].tool_call_id == "call_1"


async def test_execute_tool_error_result():
    class FailingTool(Tool):
        name: str = "failing_tool"
        description: str = ""
        parameters: dict = {}
        category: str = "general"

        async def execute(self, **kwargs) -> ToolResult:
            return ToolResult(success=False, error="tool failed")

    failing_tool = FailingTool()
    tool_call = ToolCall(id="call_1", name="failing_tool", arguments={})

    provider = MockProvider()
    registry = ToolRegistry()
    registry.register(failing_tool)
    agent = _make_agent(provider=provider, registry=registry)

    await agent.execute_tool(tool_call)

    assert len(agent.session.messages) == 1
    assert agent.session.messages[0].type == MessageType.TOOL_RESULT
    assert "Error: tool failed" in agent.session.messages[0].content


async def test_step_done_returns_false():
    provider = MockProvider()
    registry = ToolRegistry()
    agent = _make_agent(provider=provider, registry=registry)
    agent.state = AgentState.DONE

    result = await agent.step()
    assert result is False


async def test_step_max_loop_reached():
    provider = MockProvider()
    registry = ToolRegistry()
    agent = _make_agent(provider=provider, registry=registry, max_loop=3)
    agent.loop_count = 3
    agent.state = AgentState.IDLE

    result = await agent.step()
    assert result is False
    assert agent.state == AgentState.DONE
