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


def test_anthropic_multi_tool_use_merges_results_into_one_user_message():
    """assistant 一次返回多个 tool_use 时，对应 tool_result 必须合并到同一条 user 消息。

    Anthropic 协议要求：每个 tool_use 块的下一条消息中必须有 tool_result，
    且多个 tool_use 的 result 必须在同一条 user 消息的 content 数组里，
    否则 API 返回 400 "tool_use ids were found without tool_result blocks immediately after"。
    """
    messages = [
        ChatMessage(role="user", content="explore"),
        ChatMessage(
            role="assistant",
            content="calling two tools",
            tool_calls=[
                ToolCall(id="t1", name="detect_build_toolchain", arguments={}),
                ToolCall(id="t2", name="list_directory", arguments={"path": "."}),
            ],
        ),
        ChatMessage(role="tool", content="cmake found", tool_call_id="t1"),
        ChatMessage(role="tool", content="src/ tests/", tool_call_id="t2"),
        ChatMessage(role="user", content="next round"),
    ]
    _system, converted = _to_anthropic_messages(messages)

    # 期望结构：
    # [0] user "explore"
    # [1] assistant (text + 2 tool_use)
    # [2] user (content 含 2 个 tool_result)   ← 关键：合并为一条
    # [3] user "next round"
    assert len(converted) == 4

    # assistant 消息含 2 个 tool_use
    assistant_blocks = converted[1]["content"]
    tool_use_ids = [b["id"] for b in assistant_blocks if b.get("type") == "tool_use"]
    assert tool_use_ids == ["t1", "t2"]

    # tool_result 合并在同一条 user 消息
    result_msg = converted[2]
    assert result_msg["role"] == "user"
    result_blocks = result_msg["content"]
    assert len(result_blocks) == 2
    assert all(b["type"] == "tool_result" for b in result_blocks)
    assert [b["tool_use_id"] for b in result_blocks] == ["t1", "t2"]
    assert [b["content"] for b in result_blocks] == ["cmake found", "src/ tests/"]

    # 下一条是普通 user 消息
    assert converted[3] == {"role": "user", "content": "next round"}


def test_anthropic_single_tool_use_still_one_user_message():
    """单个 tool_use 对应单个 tool_result，仍应是单条 user 消息（回归测试）。"""
    messages = [
        ChatMessage(role="user", content="hi"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="s1", name="glob", arguments={"pattern": "*.py"})],
        ),
        ChatMessage(role="tool", content="a.py", tool_call_id="s1"),
    ]
    _system, converted = _to_anthropic_messages(messages)
    assert len(converted) == 3
    assert converted[2]["role"] == "user"
    assert len(converted[2]["content"]) == 1
    assert converted[2]["content"][0]["type"] == "tool_result"


def test_anthropic_consecutive_tool_rounds_separate_user_messages():
    """多轮 tool 调用：每轮的 tool_result 独立成一条 user 消息，不跨轮合并。"""
    messages = [
        ChatMessage(role="user", content="r1"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="a1", name="t", arguments={})],
        ),
        ChatMessage(role="tool", content="res1", tool_call_id="a1"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="a2", name="t", arguments={})],
        ),
        ChatMessage(role="tool", content="res2", tool_call_id="a2"),
    ]
    _system, converted = _to_anthropic_messages(messages)
    # 期望：
    # [0] user "r1"
    # [1] assistant (tool_use a1)
    # [2] user (tool_result a1)
    # [3] assistant (tool_use a2)
    # [4] user (tool_result a2)
    assert len(converted) == 5
    assert converted[2]["content"][0]["tool_use_id"] == "a1"
    assert converted[4]["content"][0]["tool_use_id"] == "a2"


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


def test_per_model_independent_api_key(tmp_path):
    """[[models]] 中独立配置的 api_key 优先于 [provider].api_key 共享池。"""
    content = """
[provider]
api_key = "shared-key"

[[models]]
id = "m1"
provider = "openai"
model = "gpt-a"
api_url = "http://a.local"
api_key = "independent-key-m1"

[[models]]
id = "m2"
provider = "anthropic"
model = "claude-b"
api_url = "http://b.local"
"""
    cfg_path = tmp_path / ".tui_agent" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(content, encoding="utf-8")

    config = Config.load(project_dir=tmp_path)
    profiles = {p.id: p for p in config.get_model_profiles()}

    assert profiles["m1"].api_key == "independent-key-m1"
    assert profiles["m2"].api_key == "shared-key"


def test_env_api_key_does_not_override_independent_key(tmp_path, monkeypatch):
    """TUI_AGENT_API_KEY 仅填充未独立配置 api_key 的模型，不覆盖已独立配置的。"""
    content = """
[provider]
api_key = "shared-key"

[[models]]
id = "m1"
provider = "openai"
model = "gpt-a"
api_url = "http://a.local"
api_key = "independent-key-m1"

[[models]]
id = "m2"
provider = "openai"
model = "gpt-b"
api_url = "http://b.local"
"""
    cfg_path = tmp_path / ".tui_agent" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(content, encoding="utf-8")

    monkeypatch.setenv("TUI_AGENT_API_KEY", "env-key")
    config = Config.load(project_dir=tmp_path)
    profiles = {p.id: p for p in config.get_model_profiles()}

    assert profiles["m1"].api_key == "independent-key-m1"
    assert profiles["m2"].api_key == "env-key"


def test_merge_models_preserves_user_level_independent_key(tmp_path, monkeypatch):
    """user 级独立 api_key 在 project 级同 id 模型未写 api_key 时仍保留。"""
    user_home = tmp_path / "home"
    user_home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user_home))
    monkeypatch.delenv("HOME", raising=False)

    user_cfg = user_home / ".tui_agent" / "config.toml"
    user_cfg.parent.mkdir(parents=True)
    user_cfg.write_text("""
[[models]]
id = "m1"
provider = "openai"
model = "gpt-a"
api_url = "http://a.local"
api_key = "user-independent-key"
""", encoding="utf-8")

    project_cfg = tmp_path / ".tui_agent" / "config.toml"
    project_cfg.parent.mkdir(parents=True)
    project_cfg.write_text("""
active_model = "m1"

[[models]]
id = "m1"
provider = "openai"
model = "gpt-a-v2"
api_url = "http://a-v2.local"
""", encoding="utf-8")

    config = Config.load(project_dir=tmp_path)
    profiles = config.get_model_profiles()

    assert len(profiles) == 1
    p = profiles[0]
    assert p.model == "gpt-a-v2"
    assert p.api_url == "http://a-v2.local"
    assert p.api_key == "user-independent-key"


def test_merge_models_appends_user_only_models(tmp_path, monkeypatch):
    """user 级独有模型在 project 级未提及时仍保留。"""
    user_home = tmp_path / "home"
    user_home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user_home))
    monkeypatch.delenv("HOME", raising=False)

    user_cfg = user_home / ".tui_agent" / "config.toml"
    user_cfg.parent.mkdir(parents=True)
    user_cfg.write_text("""
[[models]]
id = "user-only"
provider = "openai"
model = "gpt-user"
api_url = "http://user.local"
api_key = "user-key"
""", encoding="utf-8")

    project_cfg = tmp_path / ".tui_agent" / "config.toml"
    project_cfg.parent.mkdir(parents=True)
    project_cfg.write_text("""
active_model = "proj"

[[models]]
id = "proj"
provider = "openai"
model = "gpt-proj"
api_url = "http://proj.local"
""", encoding="utf-8")

    config = Config.load(project_dir=tmp_path)
    profiles = {p.id: p for p in config.get_model_profiles()}

    assert set(profiles.keys()) == {"proj", "user-only"}
    assert profiles["user-only"].api_key == "user-key"
