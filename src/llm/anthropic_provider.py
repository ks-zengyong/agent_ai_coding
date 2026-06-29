"""Anthropic Messages API provider."""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from src import debug_info
from src.llm.base import (
    BaseLLMProvider,
    ChatMessage,
    ChatResponse,
    StreamEvent,
    StreamEventType,
    ToolCall,
)

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
        max_tokens: int = 8192,
        temperature: float = 0.0,
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_secs
        self._max_retries = max_retries
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._tools: List[Dict[str, Any]] = []

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
            "temperature": self._temperature,
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
            payload=payload,
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
            raw_preview=raw_text[:300],
            raw_response=raw_text,
        )

        # 检测 max_tokens 截断：当输出因 token 上限被截断时，tool_use 的 input
        # 可能为空 {}（生成未完成）。此时若当作正常 tool_call 返回，工具会因
        # 缺参数报错（"missing positional arguments"），错误信息对 LLM 不直观，
        # 导致它反复重试同样的调用。改为丢弃空 input 的 tool_call，返回明确
        # content 提示，让 agent 的 nudge 机制引导 LLM 分步完成。
        if stop_reason == "max_tokens" and tool_calls:
            empty_input_calls = [tc for tc in tool_calls if not tc.arguments]
            if empty_input_calls:
                names = ", ".join(tc.name for tc in empty_input_calls)
                debug_info.log_info(
                    f"Output truncated (max_tokens={self._max_tokens}): "
                    f"tool_use '{names}' has empty input — dropping tool_call, "
                    f"returning guidance to LLM"
                )
                guidance = (
                    f"[Output was truncated at max_tokens={self._max_tokens}. "
                    f"The tool call for {names} was incomplete. "
                    f"Complete the task in smaller steps — write one file at a time "
                    f"with concise content, or increase max_tokens in config.]"
                )
                return ChatResponse(content=guidance, tool_calls=None)

        return ChatResponse(content=content, tool_calls=tool_calls)

    async def chat_stream(self, messages: List[ChatMessage]) -> AsyncIterator[str]:
        response = await self.chat(messages)
        for char in response.content:
            yield char

    async def chat_stream_events(
        self, messages: List[ChatMessage]
    ) -> AsyncIterator[StreamEvent]:
        """Stream chat completion as structured SSE events from Anthropic API."""
        system, anthropic_messages = _to_anthropic_messages(messages)
        payload: Dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "messages": anthropic_messages,
            "stream": True,
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
            payload=payload,
        )

        # Per-block tracking during streaming
        block_states: Dict[int, Dict[str, Any]] = {}
        finish_reason: Optional[str] = None
        usage: Optional[Dict[str, Any]] = None
        accumulated_text: str = ""

        try:
            async with httpx.AsyncClient(timeout=self._timeout, verify=False) as client:
                async with client.stream(
                    "POST",
                    self._messages_endpoint(),
                    headers=self._headers(),
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        raw_text = await response.aread()
                        raise httpx.HTTPStatusError(
                            f"API error {response.status_code}: {raw_text[:300]}",
                            request=response.request,
                            response=response,
                        )

                    async for line in response.aiter_lines():
                        if not line:
                            continue

                        # SSE: "data: <json>" or "event: <type>"
                        if line.startswith("data: "):
                            raw_data = line[len("data: "):]
                            try:
                                event_obj = json.loads(raw_data)
                            except json.JSONDecodeError:
                                continue

                            event_type = event_obj.get("type", "")

                            if event_type == "message_start":
                                usage = event_obj.get("message", {}).get("usage")

                            elif event_type == "content_block_start":
                                idx = event_obj.get("index", 0)
                                cb = event_obj.get("content_block", {})
                                block_states[idx] = {
                                    "block_type": cb.get("type", "text"),
                                    "text": "",
                                    "tool_name": cb.get("name", ""),
                                    "tool_id": cb.get("id", ""),
                                }

                            elif event_type == "content_block_delta":
                                idx = event_obj.get("index", 0)
                                delta = event_obj.get("delta", {})
                                delta_type = delta.get("type", "")
                                state = block_states.get(idx)

                                if delta_type == "thinking_delta" and state:
                                    thinking_fragment = delta.get("thinking", "")
                                    state["text"] += thinking_fragment
                                    yield StreamEvent(
                                        type=StreamEventType.THINKING_DELTA,
                                        text=thinking_fragment,
                                    )

                                elif delta_type == "text_delta" and state:
                                    text_fragment = delta.get("text", "")
                                    state["text"] += text_fragment
                                    accumulated_text += text_fragment
                                    yield StreamEvent(
                                        type=StreamEventType.TEXT_DELTA,
                                        text=text_fragment,
                                    )

                                elif delta_type == "input_json_delta" and state:
                                    state["text"] += delta.get("partial_json", "")

                            elif event_type == "content_block_stop":
                                idx = event_obj.get("index", 0)
                                state = block_states.pop(idx, None)
                                if state and state["block_type"] == "tool_use":
                                    try:
                                        parsed_args = json.loads(state["text"])
                                    except json.JSONDecodeError:
                                        parsed_args = {}
                                    yield StreamEvent(
                                        type=StreamEventType.TOOL_USE,
                                        tool_call=ToolCall(
                                            id=state["tool_id"],
                                            name=state["tool_name"],
                                            arguments=parsed_args,
                                        ),
                                    )

                            elif event_type == "message_delta":
                                delta = event_obj.get("delta", {})
                                finish_reason = delta.get("stop_reason")
                                usage = event_obj.get("usage", usage)

                            elif event_type == "message_stop":
                                pass  # handled below

                    debug_info.log_response(
                        content=accumulated_text,
                        finish_reason=finish_reason,
                        usage=usage,
                    )
                    yield StreamEvent(
                        type=StreamEventType.DONE,
                        finish_reason=finish_reason,
                        usage=usage,
                    )

        except Exception as e:
            debug_info.log_error(
                e,
                context="anthropic chat_stream_events",
                suggestion="检查 Anthropic API 流式配置与网络",
            )
            yield StreamEvent(type=StreamEventType.ERROR, text=str(e))

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

    # Anthropic 协议要求：assistant 消息中的每个 tool_use 块，在紧随其后的
    # 下一条 user 消息中必须有对应的 tool_result 块。当 assistant 一次返回
    # 多个 tool_use 时，对应的多个 tool_result 必须放在同一条 user 消息的
    # content 数组里，而非拆成多条 user 消息。否则 API 返回 400:
    #   "tool_use ids were found without tool_result blocks immediately after"
    pending_tool_results: List[Dict[str, Any]] = []

    def _flush_tool_results() -> None:
        """把累积的 tool_result 块合并为单条 user 消息并追加到输出。"""
        if not pending_tool_results:
            return
        out.append({"role": "user", "content": list(pending_tool_results)})
        pending_tool_results.clear()

    for msg in messages:
        if msg.role == "system":
            system_parts.append(msg.content or "")
            continue

        if msg.role == "tool":
            # 累积 tool_result，等遇到非 tool 消息时合并 flush
            pending_tool_results.append({
                "type": "tool_result",
                "tool_use_id": msg.tool_call_id or "",
                "content": msg.content or "",
            })
            continue

        # 遇到非 tool 消息，先 flush 累积的 tool_result
        _flush_tool_results()

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

    # 末尾若还有 tool_result（理论上不应发生），也要 flush
    _flush_tool_results()

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
                    arguments=_parse_tool_input(block.get("input"), block.get("name", "")),
                )
            )

    content = "".join(text_parts)
    return (
        content,
        tool_calls or None,
        data.get("stop_reason"),
        data.get("usage"),
    )


def _parse_tool_input(raw_input: Any, tool_name: str = "") -> Dict[str, Any]:
    """健壮解析 tool_use 的 input 字段。

    Anthropic 标准要求 ``input`` 是对象，但部分兼容网关（如 DeepSeek 的
    Anthropic 协议端点）可能返回：
    - ``null`` / 缺失 → 空参数
    - JSON 字符串 → 需 json.loads 还原（类似 OpenAI 协议的 arguments 字段）
    - 对象 → 直接使用

    当 input 为空但工具名表明需要参数时，记录警告便于诊断模型行为。
    """
    if raw_input is None:
        if tool_name:
            debug_info.log_info(
                f"tool_use '{tool_name}' returned empty input (null/missing) "
                f"— tool will fail if it requires arguments"
            )
        return {}
    if isinstance(raw_input, str):
        s = raw_input.strip()
        if not s:
            return {}
        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        debug_info.log_info(
            f"tool_use '{tool_name}' input is non-JSON string, using as-is"
        )
        return {"_raw": raw_input}
    if isinstance(raw_input, dict):
        if not raw_input and tool_name:
            debug_info.log_info(
                f"tool_use '{tool_name}' returned empty input {{}} "
                f"— tool will fail if it requires arguments"
            )
        return raw_input
    return {}
