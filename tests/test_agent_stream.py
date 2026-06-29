"""Tests for agent streaming (step_stream)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from src.agent import AgentState, CodingAgent
from src.llm.base import StreamEvent, StreamEventType, ToolCall
from src.llm.mock_provider import MockProvider
from src.tools.registry import ToolRegistry, Tool, ToolResult
from src.permissions import PermissionGuard, PermissionType


class MockReadonlyTool(Tool):
    name: str = "readonly_tool"
    description: str = "readonly"
    parameters: dict = {"type": "object", "properties": {}, "required": []}
    category: str = "readonly"

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, output="ok")


async def _collect_events(agent: CodingAgent):
    events = []
    async for event in agent.step_stream():
        events.append(event)
    return events


def _make_agent(provider, registry):
    guard = PermissionGuard()
    guard.register_tool("readonly_tool", PermissionType.READ_ONLY)
    return CodingAgent(provider=provider, tool_registry=registry, permission_guard=guard)


async def test_step_stream_text_delta_and_done():
    provider = MockProvider(responses=["Hello streaming!"])
    registry = ToolRegistry()
    agent = _make_agent(provider, registry)
    agent.state = AgentState.THINKING

    events = await _collect_events(agent)

    text = "".join(e.text for e in events if e.type == StreamEventType.TEXT_DELTA)
    assert text == "Hello streaming!"
    assert any(e.type == StreamEventType.DONE for e in events)
    assert agent.state == AgentState.DONE
    assert agent.session.messages[-1].content == "Hello streaming!"


async def test_step_stream_tool_use_schedules_execution():
    tool_call = ToolCall(id="c1", name="readonly_tool", arguments={})
    provider = MockProvider(responses=["Using tool"], tool_calls=[tool_call])
    registry = ToolRegistry()
    registry.register(MockReadonlyTool())
    agent = _make_agent(provider, registry)
    agent.state = AgentState.THINKING

    events = await _collect_events(agent)

    assert any(e.type == StreamEventType.TOOL_USE for e in events)
    assert agent.state == AgentState.EXECUTING
    assert agent.pending_tool_call is not None
    assert agent.pending_tool_call.name == "readonly_tool"


async def test_step_stream_forwards_thinking_delta():
    """Agent forwards THINKING_DELTA events from provider."""

    class ThinkingMock(MockProvider):
        async def chat_stream_events(self, messages):
            yield StreamEvent(type=StreamEventType.THINKING_DELTA, text="reasoning step")
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="answer")
            yield StreamEvent(type=StreamEventType.DONE)

    provider = ThinkingMock(responses=["answer"])
    registry = ToolRegistry()
    agent = _make_agent(provider, registry)
    agent.state = AgentState.THINKING

    events = await _collect_events(agent)

    thinking = "".join(
        e.text for e in events if e.type == StreamEventType.THINKING_DELTA
    )
    assert thinking == "reasoning step"
    assert agent.state == AgentState.DONE
