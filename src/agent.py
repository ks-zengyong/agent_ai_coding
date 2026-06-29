from __future__ import annotations

from enum import Enum
from typing import AsyncIterator, List, Optional

from src.context import Message, MessageType, Session
from src.llm.base import BaseLLMProvider, StreamEvent, StreamEventType, ToolCall
from src.permissions import PermissionGuard
from src.tools.registry import ToolRegistry


class AgentState(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    AWAITING_PERMISSION = "awaiting_permission"
    EXECUTING = "executing"
    DONE = "done"
    ERROR = "error"


class CodingAgent:
    def __init__(
        self,
        provider: BaseLLMProvider,
        tool_registry: ToolRegistry,
        permission_guard: PermissionGuard,
        max_loop: int = 100,
    ) -> None:
        self.provider = provider
        self.tool_registry = tool_registry
        self.permission_guard = permission_guard
        self.session = Session()
        self.state = AgentState.IDLE
        self.pending_tool_call: Optional[ToolCall] = None
        self.pending_tool_queue: List[ToolCall] = []
        self.loop_count = 0
        self.max_loop = max_loop
        self._empty_response_retries = 0
        self._max_empty_response_retries = 5

    def _last_real_user_index(self) -> Optional[int]:
        for i in range(len(self.session.messages) - 1, -1, -1):
            msg = self.session.messages[i]
            if msg.type == MessageType.USER and not msg.content.startswith("[System]"):
                return i
        return None

    def _has_tool_activity_since(self, start_index: int) -> bool:
        for msg in self.session.messages[start_index:]:
            if msg.type == MessageType.TOOL_RESULT:
                return True
            if msg.type == MessageType.ASSISTANT and msg.tool_calls:
                return True
        return False

    def _wants_to_continue_working(self, content: str) -> bool:
        lower = content.lower()
        continue_phrases = (
            "i'll ", "i will ", "let me ", "let's ", "i am going to ", "i'm going to ",
            "first, i", "next, i", "i need to ", "i'll start", "i will start",
            "我将", "让我", "接下来", "首先", "我需要", "先",
        )
        return any(p in lower for p in continue_phrases)

    def _looks_like_completion(self, content: str) -> bool:
        lower = content.lower()
        markers = (
            "task complete", "task completed", "successfully compiled",
            "build succeeded", "compilation successful", "all tests pass",
            "已完成", "编译成功", "构建成功", "任务完成", "成功编译",
        )
        return any(m in lower for m in markers)

    def _should_finish_without_tools(self, content: str) -> bool:
        if not content.strip():
            return False
        user_idx = self._last_real_user_index()
        if user_idx is None:
            return True
        if not self._has_tool_activity_since(user_idx):
            return False
        if self._wants_to_continue_working(content):
            return False
        if self._looks_like_completion(content):
            return True
        return True

    def _nudge_to_continue(self, reason: str) -> None:
        self._empty_response_retries += 1
        self.session.messages.pop()
        self.session.add_message(
            Message(
                type=MessageType.USER,
                content=(
                    f"[System] {reason} "
                    "You must call tools (list_directory, glob, search_code, read_file, "
                    "detect_build_toolchain, execute_command, etc.) — do not only describe plans. "
                    "Continue until the task is done or clearly blocked."
                ),
            )
        )
        self.state = AgentState.THINKING

    async def step(self) -> bool:
        if self.state == AgentState.DONE or self.state == AgentState.ERROR:
            return False
        if self.loop_count >= self.max_loop:
            self.state = AgentState.DONE
            return False

        self.loop_count += 1

        if self.state == AgentState.IDLE or self.state == AgentState.THINKING:
            await self.think()
            return self.state != AgentState.DONE and self.state != AgentState.ERROR

        if self.state == AgentState.EXECUTING:
            if self.pending_tool_call:
                await self.execute_tool(self.pending_tool_call)
                self.pending_tool_call = None
                if self.pending_tool_queue:
                    self._schedule_next_tool()
                else:
                    self.state = AgentState.THINKING
            return True

        if self.state == AgentState.AWAITING_PERMISSION:
            return True

        return False

    async def step_stream(self) -> AsyncIterator[StreamEvent]:
        """Execute one agent step with streaming events."""
        if self.state == AgentState.DONE or self.state == AgentState.ERROR:
            return
        if self.loop_count >= self.max_loop:
            self.state = AgentState.DONE
            return

        self.loop_count += 1

        if self.state == AgentState.IDLE or self.state == AgentState.THINKING:
            self.state = AgentState.THINKING
            try:
                chat_messages = self.session.to_chat_messages()
                accumulated_text = ""
                accumulated_tool_calls: List[ToolCall] = []

                async for event in self.provider.chat_stream_events(chat_messages):
                    if event.type == StreamEventType.TEXT_DELTA:
                        accumulated_text += event.text
                        yield event
                    elif event.type == StreamEventType.THINKING_DELTA:
                        yield event
                    elif event.type == StreamEventType.TOOL_USE:
                        if event.tool_call:
                            accumulated_tool_calls.append(event.tool_call)
                            yield event
                    elif event.type == StreamEventType.DONE:
                        # Build assistant message from accumulated stream
                        assistant_msg = Message(
                            type=MessageType.ASSISTANT,
                            content=accumulated_text,
                            tool_calls=accumulated_tool_calls if accumulated_tool_calls else None,
                        )
                        self.session.add_message(assistant_msg)

                        if accumulated_tool_calls:
                            self._empty_response_retries = 0
                            self.pending_tool_call = accumulated_tool_calls[0]
                            self.pending_tool_queue = list(accumulated_tool_calls[1:])
                            self._schedule_next_tool()
                        else:
                            content = accumulated_text.strip()
                            if self._should_finish_without_tools(content):
                                self._empty_response_retries = 0
                                self.state = AgentState.DONE
                            elif self._empty_response_retries < self._max_empty_response_retries:
                                if not content:
                                    reason = "Your last response was empty."
                                elif self._wants_to_continue_working(content):
                                    reason = "You described a plan but did not call any tools."
                                else:
                                    reason = "You replied with text only without tool calls."
                                self._nudge_to_continue(reason)
                            else:
                                self.state = AgentState.DONE
                        yield event
                        return
                    elif event.type == StreamEventType.ERROR:
                        raise RuntimeError(event.text or "Stream error")
            except Exception as e:
                from src import debug_info
                debug_info.log_error(e, context="agent.think", suggestion="检查 LLM 响应与工具解析")
                self.state = AgentState.ERROR
                error_msg = Message(type=MessageType.ERROR, content=str(e))
                self.session.add_message(error_msg)
                yield StreamEvent(type=StreamEventType.ERROR, text=str(e))
            return

        if self.state == AgentState.EXECUTING:
            if self.pending_tool_call:
                await self.execute_tool(self.pending_tool_call)
                self.pending_tool_call = None
                if self.pending_tool_queue:
                    self._schedule_next_tool()
                else:
                    self.state = AgentState.THINKING
            return

        if self.state == AgentState.AWAITING_PERMISSION:
            return

    async def think(self) -> None:
        self.state = AgentState.THINKING
        try:
            chat_messages = self.session.to_chat_messages()
            response = await self.provider.chat(chat_messages)

            assistant_msg = Message(
                type=MessageType.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls,
            )
            self.session.add_message(assistant_msg)

            if response.tool_calls and len(response.tool_calls) > 0:
                self._empty_response_retries = 0
                self.pending_tool_call = response.tool_calls[0]
                self.pending_tool_queue = list(response.tool_calls[1:])
                self._schedule_next_tool()
            else:
                content = (response.content or "").strip()
                if self._should_finish_without_tools(content):
                    self._empty_response_retries = 0
                    self.state = AgentState.DONE
                elif self._empty_response_retries < self._max_empty_response_retries:
                    if not content:
                        reason = "Your last response was empty."
                    elif self._wants_to_continue_working(content):
                        reason = "You described a plan but did not call any tools."
                    else:
                        reason = "You replied with text only without tool calls."
                    self._nudge_to_continue(reason)
                else:
                    self.state = AgentState.DONE
        except Exception as e:
            from src import debug_info
            debug_info.log_error(e, context="agent.think", suggestion="检查 LLM 响应与工具解析")
            self.state = AgentState.ERROR
            error_msg = Message(type=MessageType.ERROR, content=str(e))
            self.session.add_message(error_msg)

    def _schedule_next_tool(self) -> None:
        if not self.pending_tool_call and self.pending_tool_queue:
            self.pending_tool_call = self.pending_tool_queue.pop(0)
        if not self.pending_tool_call:
            self.state = AgentState.THINKING
            return
        if self.permission_guard.is_read_only(self.pending_tool_call.name):
            self.state = AgentState.EXECUTING
        else:
            self.state = AgentState.AWAITING_PERMISSION

    async def execute_tool(self, call: ToolCall) -> None:
        self.state = AgentState.EXECUTING
        try:
            result = await self.tool_registry.execute(call.name, call.arguments)
            result_msg = Message(
                type=MessageType.TOOL_RESULT,
                content=result.to_str(),
                tool_call_id=call.id,
            )
            self.session.add_message(result_msg)
        except Exception as e:
            error_msg = Message(
                type=MessageType.ERROR,
                content=str(e),
                tool_call_id=call.id,
            )
            self.session.add_message(error_msg)

    def handle_permission_denied(self, call: ToolCall) -> None:
        denied_msg = Message(
            type=MessageType.PERMISSION_DENIED,
            content=f"User denied permission for tool '{call.name}'",
            tool_call_id=call.id,
        )
        self.session.add_message(denied_msg)
        self.pending_tool_call = None
        self.pending_tool_queue = []
        self.state = AgentState.THINKING
