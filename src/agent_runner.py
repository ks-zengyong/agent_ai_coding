"""Headless agent runner for automated testing and self-feedback validation."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from src.agent import AgentState, CodingAgent
from src.context import Message, MessageType


_SYSTEM_PROMPT_TEMPLATE = """You are a terminal coding agent. Your identity is {identity}. You are NOT Claude, NOT ChatGPT, NOT any other assistant. Complete user tasks by calling tools.

Rules:
- Use read_file, list_directory, glob, search_code for exploration (auto-approved).
- Use write_file to create files, edit_file to modify, execute_command to run/tests.
- Pass tool arguments as valid JSON matching the tool schema.
- Work step by step: explore → write/edit → run/test → fix errors.
- When the task is done, reply with a short summary (no more tool calls).
- NEVER reply with only a plan ("I will explore...") — immediately call tools in the same turn.
- NEVER introduce yourself as Claude, ChatGPT, or any other brand — you are {identity}.
- NEVER run GUI programs (.exe games, windows, graphical apps) via execute_command — they block forever. Use console-only commands.

C++ / CMake / MSVC (Windows):
- ALWAYS call detect_build_toolchain first before probing compilers manually.
- For MSVC builds use execute_command with use_msvc_env=true.
- Typical flow: write CMakeLists.txt → cmake -B build -G "Visual Studio 17 2022" -A x64
  → cmake --build build --config Release (both with use_msvc_env=true).
- Empty command output still has exit_code in the result; read it carefully.
- Never end the task after empty tool output — try another approach."""


def build_system_prompt(model_identity: Optional[str] = None) -> str:
    """构造 system prompt，注入当前激活模型的身份信息。

    ``model_identity`` 为 None 时使用中性表述，避免在未知模型下写死厂商名。
    切换模型时由调用方传入新的 ``display_label()``，身份随之更新。
    """
    identity = model_identity or "the configured LLM"
    return _SYSTEM_PROMPT_TEMPLATE.format(identity=identity)


# 向后兼容：无身份信息的默认 prompt（headless / 旧调用方仍可直接引用）
SYSTEM_PROMPT = build_system_prompt()


async def run_agent_until_done(
    agent: CodingAgent,
    user_message: str,
    *,
    auto_approve: bool = True,
    max_steps: int = 200,
    on_step: Optional[Callable[[CodingAgent], None]] = None,
    model_identity: Optional[str] = None,
) -> AgentState:
    """Run agent loop until done, error, or max steps.

    ``model_identity`` 透传给 :func:`build_system_prompt`，用于在 system prompt
    中声明当前模型身份，抑制长上下文下的身份漂移。None 时使用中性表述。
    """
    if not any(m.type == MessageType.USER for m in agent.session.messages):
        agent.session.add_message(
            Message(type=MessageType.USER, content=f"[System]\n{build_system_prompt(model_identity)}")
        )

    agent.session.add_message(Message(type=MessageType.USER, content=user_message))
    agent.state = AgentState.IDLE
    agent.loop_count = 0

    for _ in range(max_steps):
        if on_step:
            on_step(agent)

        if agent.state == AgentState.AWAITING_PERMISSION:
            if auto_approve and agent.pending_tool_call:
                agent.state = AgentState.EXECUTING
            elif agent.pending_tool_call:
                agent.handle_permission_denied(agent.pending_tool_call)
                continue

        should_continue = await agent.step()
        if not should_continue:
            break

    return agent.state


def save_session(agent: CodingAgent, history_dir: Path, prefix: str = "session") -> Path:
    history_dir.mkdir(parents=True, exist_ok=True)
    path = history_dir / f"{prefix}_{int(time.time())}.json"
    agent.session.save_to_file(str(path))
    return path
