import sys
import os
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import patch

from src.context import Message, MessageType, Session
from src.llm.base import ToolCall, ChatMessage
from src.permissions import PermissionGuard, PermissionType
from src.config import Config, ProviderConfig, PermissionConfig, _merge_configs


def test_message_to_dict_without_tool_calls():
    msg = Message(type=MessageType.USER, content="hello")
    d = msg.to_dict()

    assert d["type"] == "user"
    assert d["content"] == "hello"
    assert "tool_calls" not in d
    assert "tool_call_id" not in d
    assert "timestamp" in d


def test_message_to_dict_with_tool_calls():
    tc = ToolCall(id="call_1", name="read_file", arguments={"path": "x.txt"})
    msg = Message(type=MessageType.ASSISTANT, content="", tool_calls=[tc])
    d = msg.to_dict()

    assert len(d["tool_calls"]) == 1
    assert d["tool_calls"][0]["id"] == "call_1"
    assert d["tool_calls"][0]["name"] == "read_file"
    assert d["tool_calls"][0]["arguments"] == {"path": "x.txt"}


def test_message_to_dict_with_tool_call_id():
    msg = Message(type=MessageType.TOOL_RESULT, content="result", tool_call_id="call_1")
    d = msg.to_dict()

    assert d["tool_call_id"] == "call_1"


def test_message_from_dict():
    data = {
        "type": "assistant",
        "content": "hi",
        "tool_calls": [
            {"id": "c1", "name": "read_file", "arguments": {"path": "f.txt"}}
        ],
        "timestamp": 1234567890.0,
    }
    msg = Message.from_dict(data)

    assert msg.type == MessageType.ASSISTANT
    assert msg.content == "hi"
    assert len(msg.tool_calls) == 1
    assert msg.tool_calls[0].id == "c1"
    assert msg.timestamp == 1234567890.0


def test_message_from_dict_with_tool_call_id():
    data = {"type": "tool_result", "content": "res", "tool_call_id": "c1"}
    msg = Message.from_dict(data)

    assert msg.tool_call_id == "c1"


def test_session_add_message():
    session = Session()
    assert len(session.messages) == 0

    msg = Message(type=MessageType.USER, content="test")
    session.add_message(msg)

    assert len(session.messages) == 1
    assert session.messages[0].content == "test"


def test_session_to_chat_messages_user():
    session = Session()
    session.add_message(Message(type=MessageType.USER, content="hello"))

    chat_msgs = session.to_chat_messages()
    assert len(chat_msgs) == 1
    assert chat_msgs[0].role == "user"
    assert chat_msgs[0].content == "hello"


def test_session_to_chat_messages_assistant_with_tool_calls():
    session = Session()
    tc = ToolCall(id="c1", name="read_file", arguments={})
    session.add_message(Message(type=MessageType.ASSISTANT, content="", tool_calls=[tc]))

    chat_msgs = session.to_chat_messages()
    assert len(chat_msgs) == 1
    assert chat_msgs[0].role == "assistant"
    assert len(chat_msgs[0].tool_calls) == 1
    assert chat_msgs[0].tool_calls[0].id == "c1"


def test_session_to_chat_messages_tool_result():
    session = Session()
    session.add_message(Message(type=MessageType.TOOL_RESULT, content="result", tool_call_id="c1"))

    chat_msgs = session.to_chat_messages()
    assert len(chat_msgs) == 1
    assert chat_msgs[0].role == "tool"
    assert chat_msgs[0].content == "result"
    assert chat_msgs[0].tool_call_id == "c1"


def test_session_to_chat_messages_permission_denied():
    session = Session()
    session.add_message(Message(type=MessageType.PERMISSION_DENIED, content="denied", tool_call_id="c1"))

    chat_msgs = session.to_chat_messages()
    assert len(chat_msgs) == 1
    assert chat_msgs[0].role == "tool"
    assert "Permission denied" in chat_msgs[0].content


def test_session_to_chat_messages_error():
    session = Session()
    session.add_message(Message(type=MessageType.ERROR, content="something broke"))

    chat_msgs = session.to_chat_messages()
    assert len(chat_msgs) == 1
    assert chat_msgs[0].role == "user"
    assert "Error:" in chat_msgs[0].content


def test_session_save_and_load(tmp_path):
    session = Session()
    session.add_message(Message(type=MessageType.USER, content="hello"))
    session.add_message(Message(type=MessageType.ASSISTANT, content="hi there"))

    save_path = tmp_path / "session.json"
    session.save_to_file(str(save_path))

    assert save_path.exists()
    data = json.loads(save_path.read_text(encoding="utf-8"))
    assert len(data["messages"]) == 2

    loaded = Session()
    loaded.load_from_file(str(save_path))

    assert len(loaded.messages) == 2
    assert loaded.messages[0].type == MessageType.USER
    assert loaded.messages[0].content == "hello"
    assert loaded.messages[1].type == MessageType.ASSISTANT
    assert loaded.messages[1].content == "hi there"


def test_session_compress_no_truncation_needed():
    session = Session()
    session.add_message(Message(type=MessageType.USER, content="system prompt"))
    session.add_message(Message(type=MessageType.ASSISTANT, content="short"))

    session.compress(max_tokens=1000)
    assert len(session.messages) == 2


def test_session_compress_truncates_middle_messages():
    session = Session()
    session.add_message(Message(type=MessageType.USER, content="system prompt"))
    for i in range(20):
        session.add_message(Message(type=MessageType.ASSISTANT, content="x" * 200))
        session.add_message(Message(type=MessageType.TOOL_RESULT, content="y" * 200))

    original_count = len(session.messages)
    session.compress(max_tokens=200)

    assert len(session.messages) < original_count
    assert session.messages[0].content == "system prompt"


def test_session_compress_keeps_last_two():
    session = Session()
    session.add_message(Message(type=MessageType.USER, content="system"))
    for i in range(10):
        session.add_message(Message(type=MessageType.ASSISTANT, content="a" * 100))

    session.compress(max_tokens=10)
    last_msg = session.messages[-1]
    second_last = session.messages[-2]

    assert len(session.messages) >= 3


def test_permission_guard_default_tools():
    guard = PermissionGuard()

    assert guard.is_read_only("read_file") is True
    assert guard.is_read_only("list_directory") is True
    assert guard.is_read_only("glob") is True
    assert guard.is_read_only("search_code") is True

    assert guard.is_read_only("write_file") is False
    assert guard.is_read_only("edit_file") is False
    assert guard.is_read_only("execute_command") is False


def test_permission_guard_check_permission():
    guard = PermissionGuard()

    assert guard.check_permission("read_file") == PermissionType.READ_ONLY
    assert guard.check_permission("write_file") == PermissionType.WRITE
    assert guard.check_permission("execute_command") == PermissionType.SHELL
    assert guard.check_permission("unknown_tool") is None


def test_permission_guard_register_tool():
    guard = PermissionGuard()
    guard.register_tool("custom_tool", PermissionType.READ_ONLY)

    assert guard.is_read_only("custom_tool") is True
    assert guard.check_permission("custom_tool") == PermissionType.READ_ONLY


def test_permission_guard_has_permission():
    guard = PermissionGuard()

    assert guard.has_permission("read_file", PermissionType.READ_ONLY) is True
    assert guard.has_permission("write_file", PermissionType.WRITE) is True
    assert guard.has_permission("read_file", PermissionType.WRITE) is False
    assert guard.has_permission("unknown", PermissionType.READ_ONLY) is False


def test_config_defaults():
    config = Config()

    assert config.provider.name == "mock"
    assert config.provider.model == "mock-model"
    assert config.permission.auto_exec_readonly is True
    assert config.permission.confirm_write is True
    assert config.log_level == "info"


def test_config_load_from_file(tmp_path):
    config_content = """
[provider]
name = "openai"
model = "gpt-4"
max_loop = 50

[permission]
auto_exec_readonly = false
confirm_write = false
"""
    config_file = tmp_path / ".tui_agent" / "config.toml"
    config_file.parent.mkdir(exist_ok=True)
    config_file.write_text(config_content, encoding="utf-8")

    config = Config.load(project_dir=tmp_path)

    assert config.provider.name == "openai"
    assert config.provider.model == "gpt-4"
    assert config.provider.max_loop == 50
    assert config.permission.auto_exec_readonly is False
    assert config.permission.confirm_write is False


def test_config_priority_explicit_over_project(tmp_path):
    project_config = """
[provider]
model = "project-model"
max_loop = 10
"""
    project_file = tmp_path / ".tui_agent" / "config.toml"
    project_file.parent.mkdir(exist_ok=True)
    project_file.write_text(project_config, encoding="utf-8")

    explicit_config = """
[provider]
model = "explicit-model"
max_loop = 200
"""
    explicit_file = tmp_path / "explicit.toml"
    explicit_file.write_text(explicit_config, encoding="utf-8")

    config = Config.load(project_dir=tmp_path, config_path=explicit_file)

    assert config.provider.model == "explicit-model"
    assert config.provider.max_loop == 200


def test_config_priority_project_over_user(tmp_path, monkeypatch):
    user_home = tmp_path / "home"
    user_home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(user_home))
    monkeypatch.delenv("HOME", raising=False)

    user_cfg = user_home / ".tui_agent" / "config.toml"
    user_cfg.parent.mkdir(parents=True)
    user_cfg.write_text(
        '[provider]\nmodel = "user-model"\napi_key = "user-key"\n',
        encoding="utf-8",
    )

    project_cfg = tmp_path / ".tui_agent" / "config.toml"
    project_cfg.parent.mkdir(parents=True)
    project_cfg.write_text(
        '[provider]\nmodel = "project-model"\napi_key = "project-key"\n',
        encoding="utf-8",
    )

    config = Config.load(project_dir=tmp_path)

    assert config.provider.model == "project-model"
    assert config.provider.api_key == "project-key"
    assert str(project_cfg) in config.config_sources


def test_config_priority_config_dir_over_project(tmp_path):
    project_cfg = tmp_path / ".tui_agent" / "config.toml"
    project_cfg.parent.mkdir(parents=True)
    project_cfg.write_text('[provider]\nmodel = "project-model"\n', encoding="utf-8")

    custom_dir = tmp_path / "custom_cfg"
    custom_dir.mkdir()
    (custom_dir / "config.toml").write_text(
        '[provider]\nmodel = "custom-dir-model"\n', encoding="utf-8"
    )

    config = Config.load(project_dir=tmp_path, config_dir=custom_dir)

    assert config.provider.model == "custom-dir-model"


def test_config_env_api_key_overrides_file(tmp_path, monkeypatch):
    project_cfg = tmp_path / ".tui_agent" / "config.toml"
    project_cfg.parent.mkdir(parents=True)
    project_cfg.write_text('[provider]\napi_key = "file-key"\n', encoding="utf-8")

    monkeypatch.setenv("TUI_AGENT_API_KEY", "env-key")
    config = Config.load(project_dir=tmp_path)

    assert config.provider.api_key == "env-key"
    assert "env:TUI_AGENT_API_KEY" in config.config_sources


def test_config_merge_configs():
    base = Config()
    override = Config(
        provider=ProviderConfig(name="openai", model="gpt-4"),
        permission=PermissionConfig(auto_exec_readonly=False),
    )

    merged = _merge_configs(base, override)

    assert merged.provider.name == "openai"
    assert merged.provider.model == "gpt-4"
    assert merged.permission.auto_exec_readonly is False
    assert merged.provider.max_loop == base.provider.max_loop


def test_config_merge_preserves_base_api_key():
    base = Config(provider=ProviderConfig(api_key="base-key"))
    override = Config(provider=ProviderConfig(name="openai"))

    merged = _merge_configs(base, override)

    assert merged.provider.api_key == "base-key"


def test_config_merge_tools_override_takes_priority():
    from src.config import ToolConfig

    base = Config(tools=[ToolConfig(name="tool1")])
    override = Config(tools=[ToolConfig(name="tool2"), ToolConfig(name="tool3")])

    merged = _merge_configs(base, override)

    assert len(merged.tools) == 2
    assert merged.tools[0].name == "tool2"


def test_config_history_dir_resolved_to_absolute(tmp_path):
    config_content = """
[provider]
name = "mock"
"""
    config_file = tmp_path / ".tui_agent" / "config.toml"
    config_file.parent.mkdir(exist_ok=True)
    config_file.write_text(config_content, encoding="utf-8")

    config = Config.load(project_dir=tmp_path)

    assert config.history_dir.is_absolute()
    assert str(tmp_path) in str(config.history_dir)
