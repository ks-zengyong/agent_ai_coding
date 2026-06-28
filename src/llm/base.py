"""LLM Provider base class and related types."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional


class StreamEventType(str, Enum):
    TEXT_DELTA = "text_delta"
    TOOL_USE = "tool_use"
    DONE = "done"
    ERROR = "error"


@dataclass
class StreamEvent:
    """单个流式事件。"""
    type: StreamEventType
    text: str = ""
    tool_call: Optional[ToolCall] = None
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatMessage:
    role: str
    content: str
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {"role": self.role, "content": self.content or ""}
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": (
                            tc.arguments
                            if isinstance(tc.arguments, str)
                            else json.dumps(tc.arguments, ensure_ascii=False)
                        ),
                    },
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d


@dataclass
class ChatResponse:
    content: str
    tool_calls: Optional[List[ToolCall]] = None


class BaseLLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def chat(self, messages: List[ChatMessage]) -> ChatResponse:
        """Send a chat completion request and get a full response."""
        ...

    @abstractmethod
    async def chat_stream(self, messages: List[ChatMessage]) -> AsyncIterator[str]:
        """Stream a chat completion response token by token."""
        ...

    async def chat_stream_events(self, messages: List[ChatMessage]) -> AsyncIterator[StreamEvent]:
        """Stream chat completion as structured events (text_delta / tool_use / done).

        默认实现回退到 ``chat()``，将完整响应包装为单次 DONE 事件。
        子类应覆写此方法以提供真正的流式事件。
        """
        response = await self.chat(messages)
        if response.content:
            yield StreamEvent(type=StreamEventType.TEXT_DELTA, text=response.content)
        if response.tool_calls:
            for tc in response.tool_calls:
                yield StreamEvent(type=StreamEventType.TOOL_USE, tool_call=tc)
        yield StreamEvent(type=StreamEventType.DONE)

    @abstractmethod
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get the list of available tool definitions for this provider."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The current model name."""
        ...
