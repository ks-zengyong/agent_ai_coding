"""Tests for OpenAI provider parsing helpers."""
from __future__ import annotations

import json

from src.llm.openai_provider import OpenAIProvider
from src.llm.base import ChatMessage, ToolCall


def test_chat_message_serializes_arguments_as_json_string():
    msg = ChatMessage(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id="1", name="read_file", arguments={"path": "a.py"})],
    )
    d = msg.to_dict()
    args = d["tool_calls"][0]["function"]["arguments"]
    assert isinstance(args, str)
    assert json.loads(args) == {"path": "a.py"}


def test_extract_tool_calls_from_content():
    provider = OpenAIProvider(api_url="http://localhost", api_key="k")
    content = 'I will read the file:\n```json\n{"name": "read_file", "arguments": {"path": "x"}}\n```'
    calls = provider._extract_tool_calls_from_content(content)
    assert calls is not None
    assert calls[0].name == "read_file"
    assert calls[0].arguments == {"path": "x"}


def test_parse_tool_calls_empty_name_skipped():
    provider = OpenAIProvider(api_url="http://localhost", api_key="k")
    raw = [{"id": "1", "function": {"name": "", "arguments": "{}"}}]
    assert provider._parse_tool_calls(raw) == []
