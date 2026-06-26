"""Mock LLM Provider for testing."""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List

from .base import BaseLLMProvider, ChatMessage, ChatResponse, ToolCall


class MockProvider(BaseLLMProvider):
    """Mock provider that returns pre-configured responses.

    Useful for testing and development without a real LLM.
    """

    def __init__(
        self,
        responses: List[str] | None = None,
        tool_calls: List[ToolCall] | None = None,
        model: str = "mock-model",
        tools: List[Dict[str, Any]] | None = None,
    ) -> None:
        self._responses = responses or ["Task completed successfully."]
        self._tool_calls = tool_calls
        self._model = model
        self._tools = tools or []
        self._call_count = 0

    async def chat(self, messages: List[ChatMessage]) -> ChatResponse:
        await asyncio.sleep(0.01)
        idx = min(self._call_count, len(self._responses) - 1)
        content = self._responses[idx]
        self._call_count += 1

        tool_calls = None
        if self._tool_calls and self._call_count == 1:
            tool_calls = self._tool_calls

        return ChatResponse(content=content, tool_calls=tool_calls)

    async def chat_stream(self, messages: List[ChatMessage]) -> AsyncIterator[str]:
        response = await self.chat(messages)
        for char in response.content:
            yield char
            await asyncio.sleep(0.001)

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return self._tools

    def set_tool_definitions(self, tools: List[Dict[str, Any]]) -> None:
        self._tools = tools

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def call_count(self) -> int:
        return self._call_count
