"""Tests for multi-model config, registry, and Anthropic provider."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import Config, ModelProfile
from src.llm.anthropic_provider import _parse_anthropic_response, _to_anthropic_messages, _to_anthropic_tools
from src.llm.base import ChatMessage, ToolCall
from src.llm.model_registry import ModelRegistry


def test_config_loads_multiple_models(tmp_path, monkeypatch):
    user_home = tmp_path / "home"
    user_home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user_home))
    monkeypatch.delenv("HOME", raising=False)

    content = """
[provider]
api_key = "shared-key"
max_loop = 50

active_model = "m2"

[[models]]
id = "m1"
label = "OpenAI Model"
provider = "openai"
model = "gpt-test"
api_url = "http://openai.local"

[[models]]
id = "m2"
label = "Anthropic Model"
provider = "anthropic"
model = "claude-test"
api_url = "http://anthropic.local"
"""
    cfg_path = tmp_path / ".tui_agent" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(content, encoding="utf-8")

    config = Config.load(project_dir=tmp_path)
    profiles = config.get_model_profiles()

    assert len(profiles) == 2
    assert profiles[0].api_key == "shared-key"
    assert config.get_active_model_id() == "m2"


def test_config_fallback_single_provider():
    config = Config(provider=__import__("src.config", fromlist=["ProviderConfig"]).ProviderConfig(
        name="openai", model="solo-model", api_url="http://x"
    ))
    profiles = config.get_model_profiles()
    assert len(profiles) == 1
    assert profiles[0].id == "default"
    assert profiles[0].model == "solo-model"


def test_model_registry_switch_by_index():
    config = Config(
        models=[
            ModelProfile(id="a", label="A", provider="mock", model="m1"),
            ModelProfile(id="b", label="B", provider="mock", model="m2"),
        ],
        active_model="a",
    )
    registry = ModelRegistry(config)
    profile = registry.switch("2")
    assert profile.id == "b"
    assert registry.active_id == "b"


def test_model_registry_switch_by_id():
    config = Config(
        models=[
            ModelProfile(id="openai-1", provider="openai", model="m1"),
            ModelProfile(id="anthropic-1", provider="anthropic", model="m2"),
        ],
    )
    registry = ModelRegistry(config)
    profile = registry.switch("anthropic-1")
    assert profile.provider == "anthropic"


def test_anthropic_message_conversion():
    messages = [
        ChatMessage(role="user", content="hello"),
        ChatMessage(
            role="assistant",
            content="calling",
            tool_calls=[ToolCall(id="t1", name="read_file", arguments={"path": "a.py"})],
        ),
        ChatMessage(role="tool", content="file body", tool_call_id="t1"),
    ]
    system, converted = _to_anthropic_messages(messages)
    assert system == ""
    assert converted[0]["role"] == "user"
    assert converted[1]["content"][1]["type"] == "tool_use"
    assert converted[2]["content"][0]["type"] == "tool_result"


def test_anthropic_tool_conversion():
    tools = [{
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "read",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }]
    out = _to_anthropic_tools(tools)
    assert out[0]["name"] == "read_file"
    assert "input_schema" in out[0]


def test_parse_anthropic_response_with_tool_use():
    raw = json.dumps({
        "content": [
            {"type": "text", "text": "done"},
            {"type": "tool_use", "id": "tu1", "name": "glob", "input": {"pattern": "*.py"}},
        ],
        "stop_reason": "tool_use",
    })
    content, tool_calls, stop_reason, _ = _parse_anthropic_response(raw)
    assert content == "done"
    assert tool_calls is not None
    assert tool_calls[0].name == "glob"
    assert stop_reason == "tool_use"
