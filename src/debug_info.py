"""Unified debug logging for LLM requests, responses, and errors.

日志分级：
- ``info``（默认）：仅显示 ``log_error``、``log_info``——错误和运行提示对用户有价值
- ``debug``：额外显示 ``log_request``、``log_response``——完整请求/响应摘要，用于排查

非 debug 模式下，TUI 对话交互中不会出现 LLM Request / LLM Response panel，
避免刷屏；错误和关键运行提示（如 max_tokens 截断引导）仍会展示。

会话持久化：
- ``start_session(history_dir)`` 生成唯一 session_id，后续每次 ``log_request``
  自动将完整请求载荷写入 ``{history_dir}/{session_id}/{idx:04d}.log``
- 无需手动调用写入函数，provider 传 ``payload`` 参数即可触发
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel

_console = Console(stderr=True)
_enabled = True
_log_level = "info"

# ── session state ──────────────────────────────────────────────
_session_id: Optional[str] = None
_request_idx: int = 0
_history_dir: Path = Path(".ai_history")


def start_session(history_dir: Path | str = ".ai_history") -> str:
    """开始一个对话 session，生成唯一 ID，重置请求计数器。

    返回 session_id，后续每次 ``log_request`` 自动递增 idx 并写入文件。
    """
    global _session_id, _request_idx, _history_dir
    _session_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    _request_idx = 0
    _history_dir = Path(history_dir)
    return _session_id


def _write_request_payload(
    provider: str,
    api_url: str,
    api_key: Optional[str],
    payload: Dict[str, Any],
    timeout_secs: int = 60,
    max_retries: int = 3,
) -> None:
    """将完整请求写入 ``{history_dir}/{session_id}/request_{idx:04d}.log``。"""
    if not _session_id:
        return
    global _request_idx
    _request_idx += 1

    record = {
        "idx": _request_idx,
        "provider": provider,
        "url": api_url,
        "method": "POST",
        "timeout_secs": timeout_secs,
        "max_retries": max_retries,
        "api_key": _mask_key(api_key),
        "payload": payload,
    }

    dir_path = _history_dir / _session_id
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / f"request_{_request_idx:04d}.log"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2, default=str)


def _write_response_payload(
    raw_response: str,
    parsed: Optional[Dict[str, Any]] = None,
) -> None:
    """将完整响应写入 ``{history_dir}/{session_id}/response_{idx:04d}.log``。

    写入当前 ``_request_idx`` 对应的编号（与 request 成对），
    同时保存原始 JSON 文本和解析后的结构化摘要。
    """
    if not _session_id:
        return
    dir_path = _history_dir / _session_id
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / f"response_{_request_idx:04d}.log"

    record: Dict[str, Any] = {
        "idx": _request_idx,
        "raw": raw_response,
    }
    if parsed:
        record["parsed"] = parsed

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2, default=str)


def configure(enabled: bool = True, log_level: str = "info") -> None:
    global _enabled, _log_level
    _enabled = enabled
    _log_level = log_level


def _should_log_debug() -> bool:
    """是否输出 debug 级别日志（request/response panel）。"""
    return _enabled and _log_level == "debug"


def _truncate(text: str, limit: int = 200) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _mask_key(key: Optional[str]) -> str:
    if not key:
        return "(none)"
    if len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]


def _summarize_message(msg: Any) -> str:
    """把单条消息压缩为一行摘要：role + content 长度 + tool_calls 名称列表。

    不展示 content 实际内容，只展示规模信息，让用户了解本次发送了什么类别的数据。
    """
    role = getattr(msg, "role", msg.get("role", "?") if isinstance(msg, dict) else "?")
    content = getattr(msg, "content", msg.get("content", "") if isinstance(msg, dict) else "")
    content_str = str(content or "")
    parts = [f"[{role}]"]

    if content_str:
        parts.append(f"content({len(content_str)} chars)")

    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        names = [tc.name for tc in tool_calls]
        parts.append(f"tool_calls={names}")

    return " ".join(parts)


def log_request(
    *,
    provider: str,
    model: str,
    api_url: str,
    api_key: Optional[str],
    messages: List[Any],
    tools: Optional[List[Dict[str, Any]]] = None,
    timeout_secs: int = 60,
    max_retries: int = 3,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """记录本次 LLM 请求。

    - 仅 debug 模式输出 panel 摘要
    - 若 ``payload`` 不为 None，写出完整请求到 ``{history_dir}/{session_id}/{idx:04d}.log``
      （需先调用 ``start_session()``）
    """
    # 文件写入：payload 不空 + session 已启动 → 写出完整请求
    if payload is not None:
        _write_request_payload(
            provider=provider,
            api_url=api_url,
            api_key=api_key,
            payload=payload,
            timeout_secs=timeout_secs,
            max_retries=max_retries,
        )

    if not _should_log_debug():
        return

    lines = [
        f"Provider: {provider} | Model: {model}",
        f"URL: {api_url} | API Key: {_mask_key(api_key)}",
        f"Messages: {len(messages)} | Timeout: {timeout_secs}s | Retries: {max_retries}",
    ]
    for i, msg in enumerate(messages):
        lines.append(f"  {i}: {_summarize_message(msg)}")

    if tools:
        tool_names = [t.get("function", {}).get("name", "?") for t in tools]
        lines.append(f"Tools ({len(tools)}): {', '.join(tool_names)}")

    _console.print(Panel("\n".join(lines), title="LLM Request", border_style="blue"))


def log_response(
    *,
    content: str = "",
    tool_calls: Optional[List[Any]] = None,
    finish_reason: Optional[str] = None,
    usage: Optional[Dict[str, Any]] = None,
    raw_preview: Optional[str] = None,
    raw_response: Optional[str] = None,
) -> None:
    """记录本次 LLM 响应。

    - 仅 debug 模式输出 panel 类别摘要
    - 若 ``raw_response`` 不为 None，写出完整响应到
      ``{history_dir}/{session_id}/response_{idx:04d}.log``
    """
    # 文件写入：raw_response 不空 + session 已启动 → 写出完整响应
    if raw_response is not None:
        _write_response_payload(raw_response=raw_response)

    if not _should_log_debug():
        return

    lines: List[str] = []
    if finish_reason:
        lines.append(f"Finish: {finish_reason}")
    if content:
        lines.append(f"Content: {len(content)} chars")
    else:
        lines.append("Content: (empty)")
    if tool_calls:
        names = [
            getattr(tc, "name", tc.get("name", "?") if isinstance(tc, dict) else "?")
            for tc in tool_calls
        ]
        lines.append(f"Tool calls: {names}")
    if usage:
        lines.append(f"Usage: {usage}")
    if raw_preview:
        lines.append(f"Raw: {_truncate(raw_preview, 200)}")

    _console.print(Panel("\n".join(lines), title="LLM Response", border_style="green"))


def log_error(
    error: Exception | str,
    *,
    context: str = "",
    suggestion: str = "",
    attempt: Optional[int] = None,
    max_attempts: Optional[int] = None,
) -> None:
    """始终输出——错误信息对用户排查问题有价值。"""
    if not _enabled:
        return

    err_type = type(error).__name__ if isinstance(error, Exception) else "Error"
    err_msg = str(error)
    lines = [f"Type: {err_type}", f"Message: {err_msg}"]
    if context:
        lines.append(f"Context: {context}")
    if attempt is not None and max_attempts is not None:
        lines.append(f"Retry: {attempt}/{max_attempts}")
    if suggestion:
        lines.append(f"Suggestion: {suggestion}")

    _console.print(Panel("\n".join(lines), title="LLM Error", border_style="red"))


def log_info(message: str) -> None:
    """始终输出——运行提示（如 max_tokens 截断引导）对用户有价值。"""
    if _enabled:
        _console.print(f"[cyan]ℹ[/cyan] {message}")
