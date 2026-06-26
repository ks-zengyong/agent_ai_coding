"""LLM Provider base class and related types."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional


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

    @abstractmethod
    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Get the list of available tool definitions for this provider."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The current model name."""
        ...
