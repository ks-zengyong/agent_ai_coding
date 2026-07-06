"""Tests for prefix cache manager and agent integration."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio

from src.agent import AgentState, CodingAgent
from src.agent_runner import build_system_prompt, ensure_session_system_message
from src.context import Message, MessageType
from src.llm.base import ChatMessage, StreamEvent, StreamEventType
from src.llm.mock_provider import MockProvider
from src.llm.prefix_cache import (
    PrefixCacheManager,
    extract_cached_tokens,
    extract_prompt_tokens,
    filter_session_system_messages,
)
from src.permissions import PermissionGuard
from src.tools.registry import ToolRegistry


def _sample_tools() -> list:
    return [
        {"function": {"name": "read_file", "description": "read", "parameters": {}}},
        {"function": {"name": "write_file", "description": "write", "parameters": {}}},
    ]


def test_extract_cached_tokens_openai_shape():
    usage = {"prompt_tokens": 100, "cached_tokens": 80}
    assert extract_cached_tokens(usage) == 80
    assert extract_prompt_tokens(usage) == 100


def test_extract_cached_tokens_anthropic_shape():
    usage = {"input_tokens": 200, "cache_read_input_tokens": 150}
    assert extract_cached_tokens(usage) == 150
    assert extract_prompt_tokens(usage) == 200


def test_filter_session_system_messages():
    history = [
        ChatMessage(role="user", content="[System]\nlegacy prompt"),
        ChatMessage(role="user", content="real task"),
    ]
    filtered = filter_session_system_messages(history)
    assert len(filtered) == 1
    assert filtered[0].content == "real task"


def test_build_messages_uses_system_role_only():
    mgr = PrefixCacheManager(
        system_prompt=build_system_prompt("Test Model"),
        tools=_sample_tools(),
    )
    history = [
        ChatMessage(role="user", content="[System]\nshould drop"),
        ChatMessage(role="user", content="hello"),
    ]
    messages = mgr.build_messages(history)
    assert messages[0].role == "system"
    assert "Test Model" in messages[0].content
    assert len(messages) == 2
    assert messages[1].content == "hello"


def test_record_usage_counts_cache_hit():
    mgr = PrefixCacheManager(system_prompt="sys", tools=_sample_tools())
    mgr.record_usage({"input_tokens": 100, "cache_read_input_tokens": 60})
    assert mgr.metrics.cache_hits == 1
    assert mgr.metrics.saved_tokens == 60


def test_ensure_session_system_skipped_with_prefix_cache():
    registry = ToolRegistry()
    agent = CodingAgent(
        provider=MockProvider(),
        tool_registry=registry,
        permission_guard=PermissionGuard(),
        prefix_cache=PrefixCacheManager(
            system_prompt=build_system_prompt("M"),
            tools=_sample_tools(),
        ),
    )
    ensure_session_system_message(agent, "M")
    assert len(agent.session.messages) == 0


def test_ensure_session_system_injected_without_prefix_cache():
    registry = ToolRegistry()
    agent = CodingAgent(
        provider=MockProvider(),
        tool_registry=registry,
        permission_guard=PermissionGuard(),
    )
    ensure_session_system_message(agent, "M")
    assert len(agent.session.messages) == 1
    assert agent.session.messages[0].content.startswith("[System]")


class _UsageMock(MockProvider):
    async def chat_stream_events(self, messages):
        yield StreamEvent(type=StreamEventType.TEXT_DELTA, text="ok")
        yield StreamEvent(
            type=StreamEventType.DONE,
            usage={"prompt_tokens": 10, "cached_tokens": 7},
        )


def test_step_stream_records_prefix_cache_usage():
    registry = ToolRegistry()
    prefix_cache = PrefixCacheManager(
        system_prompt=build_system_prompt("M"),
        tools=_sample_tools(),
    )
    agent = CodingAgent(
        provider=_UsageMock(responses=["ok"]),
        tool_registry=registry,
        permission_guard=PermissionGuard(),
        prefix_cache=prefix_cache,
        cache_log_interval=0,
    )
    agent.state = AgentState.THINKING

    async def _run():
        async for _ in agent.step_stream():
            pass

    asyncio.get_event_loop().run_until_complete(_run())
    assert prefix_cache.metrics.total_requests == 1
    assert prefix_cache.metrics.cache_hits == 1
