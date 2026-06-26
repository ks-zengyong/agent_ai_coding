"""Unified debug logging for LLM requests, responses, and errors."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel

_console = Console(stderr=True)
_enabled = True
_log_level = "info"


def configure(enabled: bool = True, log_level: str = "info") -> None:
    global _enabled, _log_level
    _enabled = enabled
    _log_level = log_level


def _should_log_debug() -> bool:
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
) -> None:
    if not _enabled:
        return

    lines = [
        f"Provider: {provider}",
        f"Model: {model}",
        f"URL: {api_url}",
        f"API Key: {_mask_key(api_key)}",
        f"Messages: {len(messages)}",
        f"Timeout: {timeout_secs}s | Retries: {max_retries}",
    ]
    for i, msg in enumerate(messages):
        role = getattr(msg, "role", msg.get("role", "?") if isinstance(msg, dict) else "?")
        content = getattr(msg, "content", msg.get("content", "") if isinstance(msg, dict) else "")
        lines.append(f"  [{i}] {role}: {_truncate(str(content or ''), 150)}")
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            names = [tc.name for tc in tool_calls]
            lines.append(f"       tool_calls: {names}")

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
) -> None:
    if not _enabled:
        return

    lines = [f"Content: {_truncate(content, 300)}"]
    if tool_calls:
        for tc in tool_calls:
            name = getattr(tc, "name", tc.get("name", "?") if isinstance(tc, dict) else "?")
            args = getattr(tc, "arguments", tc.get("arguments", {}) if isinstance(tc, dict) else {})
            lines.append(f"Tool: {name}({_truncate(json.dumps(args, ensure_ascii=False), 120)})")
    if finish_reason:
        lines.append(f"Finish: {finish_reason}")
    if usage:
        lines.append(f"Usage: {usage}")
    if raw_preview and _should_log_debug():
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
    if _enabled:
        _console.print(f"[cyan]ℹ[/cyan] {message}")
