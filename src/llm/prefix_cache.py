"""Prefix cache manager for DeepSeek Context Caching (prefix-cache).

DeepSeek API supports automatic prefix caching of KV Cache:
- Server caches the prompt prefix automatically
- Subsequent requests with the same byte-level prefix hit the cache
- Only incremental parts need recomputation, reducing token cost by 90%+

Anthropic-compatible gateways may report ``cache_read_input_tokens`` instead.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from src.llm.base import ChatMessage


@dataclass
class CacheMetrics:
    """Track cache hit/miss statistics."""

    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    saved_tokens: int = 0
    total_prompt_tokens: int = 0

    def record(self, prompt_tokens: int, cached_tokens: int = 0) -> None:
        self.total_requests += 1
        self.total_prompt_tokens += prompt_tokens
        if cached_tokens > 0:
            self.cache_hits += 1
            self.saved_tokens += cached_tokens
        else:
            self.cache_misses += 1

    @property
    def hit_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.cache_hits / self.total_requests

    def summary(self) -> str:
        return (
            f"Cache: {self.cache_hits}/{self.total_requests} hits "
            f"({self.hit_rate:.1%}), saved {self.saved_tokens} tokens"
        )


def extract_prompt_tokens(usage: Dict[str, Any]) -> int:
    """Normalize prompt/input token counts across OpenAI and Anthropic usage."""
    for key in ("prompt_tokens", "input_tokens"):
        value = usage.get(key)
        if value is not None:
            return int(value)
    return 0


def extract_cached_tokens(usage: Dict[str, Any]) -> int:
    """Normalize cache-hit token counts across provider usage shapes."""
    for key in (
        "cached_tokens",
        "cache_read_input_tokens",
        "prompt_cache_hit_tokens",
    ):
        value = usage.get(key)
        if value:
            return int(value)
    return 0


def is_session_system_user_content(content: str) -> bool:
    """True for legacy session-injected ``[System]`` user messages."""
    return (content or "").startswith("[System]")


def filter_session_system_messages(history: List[ChatMessage]) -> List[ChatMessage]:
    """Drop duplicate ``[System]`` user rows when API system prompt is used."""
    return [
        msg
        for msg in history
        if not (msg.role == "user" and is_session_system_user_content(msg.content or ""))
    ]


class PrefixCacheManager:
    """Manage immutable prefix for DeepSeek / Anthropic prefix-cache.

    Ensures that system prompt and tool definitions remain stable across requests,
    maximizing the probability of cache hits on the server side.
    """

    def __init__(
        self,
        system_prompt: str,
        tools: List[Dict[str, Any]],
    ) -> None:
        self.system_prompt = system_prompt
        self.tools = self._sort_tools(tools)
        self._fingerprint = self._compute_fingerprint()
        self.metrics = CacheMetrics()

    def _sort_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sort tool definitions by name to ensure stable ordering."""
        return sorted(
            tools,
            key=lambda t: t.get("function", {}).get("name", ""),
        )

    def _compute_fingerprint(self) -> str:
        """Compute SHA-256 fingerprint of the immutable prefix."""
        prefix = {
            "system": self.system_prompt,
            "tools": self.tools,
        }
        payload = json.dumps(prefix, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    def update_prefix(
        self,
        system_prompt: str,
        tools: List[Dict[str, Any]],
    ) -> bool:
        """Replace immutable prefix (e.g. after model switch). Returns True if changed."""
        sorted_tools = self._sort_tools(tools)
        if system_prompt == self.system_prompt and sorted_tools == self.tools:
            return False
        self.system_prompt = system_prompt
        self.tools = sorted_tools
        self._fingerprint = self._compute_fingerprint()
        return True

    def sync_tools(self, tools: List[Dict[str, Any]]) -> bool:
        """Refresh tool definitions if they changed. Returns True if fingerprint changed."""
        sorted_tools = self._sort_tools(tools)
        if sorted_tools == self.tools:
            return False
        self.tools = sorted_tools
        self._fingerprint = self._compute_fingerprint()
        return True

    def build_messages(
        self,
        history: List[ChatMessage],
    ) -> List[ChatMessage]:
        """Build full message list: immutable system prefix + conversation history."""
        prefix = [ChatMessage(role="system", content=self.system_prompt)]
        return prefix + filter_session_system_messages(history)

    def verify_stable(self, current_tools: List[Dict[str, Any]]) -> bool:
        """Verify that current tools match the cached prefix fingerprint."""
        sorted_tools = self._sort_tools(current_tools)
        test_prefix = {
            "system": self.system_prompt,
            "tools": sorted_tools,
        }
        payload = json.dumps(test_prefix, sort_keys=True, ensure_ascii=False)
        current_fp = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return current_fp == self._fingerprint

    def record_usage(self, usage: Optional[Dict[str, Any]]) -> None:
        """Record token usage and detect cache hits from API response."""
        if not usage:
            return
        prompt_tokens = extract_prompt_tokens(usage)
        cached_tokens = extract_cached_tokens(usage)
        self.metrics.record(prompt_tokens, cached_tokens)

    def get_metrics(self) -> CacheMetrics:
        return self.metrics
