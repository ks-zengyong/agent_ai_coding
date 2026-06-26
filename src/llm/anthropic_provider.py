"""Anthropic Messages API provider."""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from src import debug_info
from src.llm.base import BaseLLMProvider, ChatMessage, ChatResponse, ToolCall

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(BaseLLMProvider):
    def __init__(
        self,
        api_url: str,
        api_key: str | None = None,
        model: str = "claude-3-5-sonnet-20241022",
        timeout_secs: int = 60,
        max_retries: int = 3,
        log_level: str = "info",
        max_tokens: int = 4096,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_secs
        self._max_retries = max_retries
        self._max_tokens = max_tokens
        self._tools: List[Dict[str, Any]] = []
        debug_info.configure(enabled=True, log_level=log_level)

    def _messages_endpoint(self) -> str:
        if self._api_url.endswith("/v1/messages"):
            return self._api_url
        return f"{self._api_url}/v1/messages"

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    async def chat(self, messages: List[ChatMessage]) -> ChatResponse:
        return await self._with_retry(self._chat_impl, messages)

    async def _chat_impl(self, messages: List[ChatMessage]) -> ChatResponse:
        system, anthropic_messages = _to_anthropic_messages(messages)
        payload: Dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": anthropic_messages,
        }
        if system:
            payload["system"] = system
        if self._tools:
            payload["tools"] = _to_anthropic_tools(self._tools)

        debug_info.log_request(
            provider="anthropic",
            model=self._model,
            api_url=self._messages_endpoint(),
            api_key=self._api_key,
            messages=messages,
            tools=self._tools,
            timeout_secs=self._timeout,
            max_retries=self._max_retries,
        )

        async with httpx.AsyncClient(timeout=self._timeout, verify=False) as client:
            response = await client.post(
                self._messages_endpoint(),
                headers=self._headers(),
                json=payload,
            )
            raw_text = response.text
            if response.status_code != 200:
                raise httpx.HTTPStatusError(
                    f"API error {response.status_code}: {raw_text[:300]}",
                    request=response.request,
                    response=response,
                )

        content, tool_calls, stop_reason, usage = _parse_anthropic_response(raw_text)
        debug_info.log_response(
            content=content,
            tool_calls=tool_calls,
            finish_reason=stop_reason,
            usage=usage,
            raw_preview=raw_text[:300] if debug_info._should_log_debug() else None,
        )
        return ChatResponse(content=content, tool_calls=tool_calls)

    async def chat_stream(self, messages: List[ChatMessage]) -> AsyncIterator[str]:
        response = await self.chat(messages)
        for char in response.content:
            yield char

    async def _with_retry(self, func, *args, **kwargs):
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_exc = e
                debug_info.log_error(
                    e,
                    context="anthropic chat",
                    suggestion="检查 Anthropic API 配置与网络",
                    attempt=attempt + 1,
                    max_attempts=self._max_retries,
                )
                if attempt < self._max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        if last_exc:
            raise last_exc
        raise RuntimeError("Retry failed without exception")

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return self._tools

    def set_tool_definitions(self, tools: List[Dict[str, Any]]) -> None:
        self._tools = tools

    @property
    def model_name(self) -> str:
        return self._model


def _to_anthropic_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for tool in tools:
        fn = tool.get("function", {})
        result.append({
            "name": fn.get("name", ""),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return result


def _to_anthropic_messages(
    messages: List[ChatMessage],
) -> tuple[str, List[Dict[str, Any]]]:
    system_parts: List[str] = []
    out: List[Dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system":
            system_parts.append(msg.content or "")
            continue

        if msg.role == "user":
            out.append({"role": "user", "content": msg.content or ""})
            continue

        if msg.role == "assistant":
            blocks: List[Dict[str, Any]] = []
            if msg.content:
                blocks.append({"type": "text", "text": msg.content})
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments,
                    })
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            continue

        if msg.role == "tool":
            out.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": msg.tool_call_id or "",
                    "content": msg.content or "",
                }],
            })

    return "\n".join(system_parts).strip(), out


def _parse_anthropic_response(
    raw_text: str,
) -> tuple[str, Optional[List[ToolCall]], Optional[str], Optional[Dict[str, Any]]]:
    data = json.loads(raw_text)
    blocks = data.get("content", [])
    text_parts: List[str] = []
    tool_calls: List[ToolCall] = []

    for block in blocks:
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=block.get("id", ""),
                    name=block.get("name", ""),
                    arguments=block.get("input", {}) or {},
                )
            )

    content = "".join(text_parts)
    return (
        content,
        tool_calls or None,
        data.get("stop_reason"),
        data.get("usage"),
    )
