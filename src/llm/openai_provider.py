"""OpenAI-compatible LLM Provider."""
from __future__ import annotations

import asyncio
import json
import re
import urllib3
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from src import debug_info

from .base import BaseLLMProvider, ChatMessage, ChatResponse, ToolCall


class OpenAIProvider(BaseLLMProvider):
    """OpenAI-compatible API provider with streaming, retry, and timeout support."""

    def __init__(
        self,
        api_url: str,
        api_key: str | None = None,
        model: str = "gpt-4",
        timeout_secs: int = 60,
        max_retries: int = 3,
        log_level: str = "info",
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_secs
        self._max_retries = max_retries
        self._tools: List[Dict[str, Any]] = []
        debug_info.configure(enabled=True, log_level=log_level)

    def _get_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def chat(self, messages: List[ChatMessage]) -> ChatResponse:
        return await self._with_retry(self._chat_impl, messages)

    async def _chat_impl(self, messages: List[ChatMessage]) -> ChatResponse:
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": [m.to_dict() for m in messages],
        }
        if self._tools:
            payload["tools"] = self._tools
            payload["tool_choice"] = "auto"

        debug_info.log_request(
            provider="openai",
            model=self._model,
            api_url=self._api_url,
            api_key=self._api_key,
            messages=messages,
            tools=self._tools,
            timeout_secs=self._timeout,
            max_retries=self._max_retries,
        )

        async with httpx.AsyncClient(timeout=self._timeout, verify=False) as client:
            response = await client.post(
                f"{self._api_url}/chat/completions",
                headers=self._get_headers(),
                json=payload,
            )

            raw_text = response.text
            if response.status_code != 200:
                raise httpx.HTTPStatusError(
                    f"API error {response.status_code}: {raw_text[:300]}",
                    request=response.request,
                    response=response,
                )

            full_content, tool_calls, finish_reason, usage = self._parse_response(raw_text)

        debug_info.log_response(
            content=full_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            raw_preview=raw_text[:300] if debug_info._should_log_debug() else None,
        )

        return ChatResponse(content=full_content, tool_calls=tool_calls)

    def _parse_response(
        self, raw_text: str
    ) -> tuple[str, Optional[List[ToolCall]], Optional[str], Optional[Dict[str, Any]]]:
        """Parse JSON or SSE response into content and tool calls."""
        full_content = ""
        tool_calls: Optional[List[ToolCall]] = None
        finish_reason: Optional[str] = None
        usage: Optional[Dict[str, Any]] = None
        msg: Optional[Dict[str, Any]] = None

        try:
            data = json.loads(raw_text)
            choice = data.get("choices", [{}])[0]
            msg = choice.get("message", {})
            full_content = msg.get("content", "") or ""
            finish_reason = choice.get("finish_reason")
            usage = data.get("usage")
        except (json.JSONDecodeError, ValueError):
            full_content, msg, finish_reason = self._parse_sse(raw_text)

        if msg and msg.get("tool_calls"):
            tool_calls = self._parse_tool_calls(msg["tool_calls"])

        # Fallback: extract tool call from content text (some APIs embed JSON in content)
        if not tool_calls and full_content:
            tool_calls = self._extract_tool_calls_from_content(full_content)

        return full_content, tool_calls, finish_reason, usage

    def _parse_sse(self, raw_text: str) -> tuple[str, Optional[Dict[str, Any]], Optional[str]]:
        accumulated: Dict[str, Any] = {"content": "", "tool_calls": []}
        finish_reason: Optional[str] = None

        for line in raw_text.splitlines():
            line = line.strip()
            if not line.startswith("data: "):
                continue
            data_str = line[6:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                if "content" in delta and delta["content"]:
                    accumulated["content"] += delta["content"]
                if "tool_calls" in delta and delta["tool_calls"]:
                    for tc in delta["tool_calls"]:
                        idx = tc.get("index", 0)
                        while len(accumulated["tool_calls"]) <= idx:
                            accumulated["tool_calls"].append({
                                "id": "",
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            })
                        entry = accumulated["tool_calls"][idx]
                        if tc.get("id"):
                            entry["id"] = tc["id"]
                        fn = tc.get("function", {})
                        if fn.get("name"):
                            entry["function"]["name"] = fn["name"]
                        if fn.get("arguments"):
                            entry["function"]["arguments"] += fn["arguments"]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
            except (json.JSONDecodeError, KeyError, IndexError):
                continue

        msg = accumulated if accumulated["tool_calls"] else None
        return accumulated["content"], msg, finish_reason

    def _parse_tool_calls(self, raw_calls: List[Dict[str, Any]]) -> List[ToolCall]:
        result: List[ToolCall] = []
        for tc in raw_calls:
            func = tc.get("function", {})
            name = func.get("name", "")
            if not name:
                continue
            args = func.get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    args = {"_raw": args}
            result.append(ToolCall(id=tc.get("id", ""), name=name, arguments=args))
        return result

    def _extract_tool_calls_from_content(self, content: str) -> Optional[List[ToolCall]]:
        """Parse tool calls embedded in markdown code blocks or raw JSON."""
        patterns = [
            r"```(?:json)?\s*(\{.*?\})\s*```",
            r'(\{"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{.*?\}\})',
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, content, re.DOTALL):
                try:
                    data = json.loads(match.group(1))
                    if "name" in data:
                        return [
                            ToolCall(
                                id="extracted-0",
                                name=data["name"],
                                arguments=data.get("arguments", {}),
                            )
                        ]
                except json.JSONDecodeError:
                    continue
        return None

    async def chat_stream(self, messages: List[ChatMessage]) -> AsyncIterator[str]:
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": [m.to_dict() for m in messages],
            "stream": True,
        }
        if self._tools:
            payload["tools"] = self._tools

        async with httpx.AsyncClient(timeout=self._timeout, verify=False) as client:
            async with client.stream(
                "POST",
                f"{self._api_url}/chat/completions",
                headers=self._get_headers(),
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta and delta["content"]:
                                yield delta["content"]
                        except (json.JSONDecodeError, KeyError):
                            continue

    async def _with_retry(self, func, *args, **kwargs):
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_exc = e
                suggestion = "检查 API 配置与网络连接"
                if "429" in str(e):
                    suggestion = "触发限流，等待后重试"
                elif "401" in str(e) or "403" in str(e):
                    suggestion = "检查 API Key 是否正确"
                debug_info.log_error(
                    e,
                    context="chat completion",
                    suggestion=suggestion,
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
