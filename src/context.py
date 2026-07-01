from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from src.llm.base import ChatMessage, ToolCall


class MessageType(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_RESULT = "tool_result"
    PERMISSION_DENIED = "permission_denied"
    ERROR = "error"


@dataclass
class Message:
    type: MessageType
    content: str
    tool_calls: Optional[List[ToolCall]] = None
    tool_call_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = {
            "type": self.type.value,
            "content": self.content,
            "timestamp": self.timestamp,
        }
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "name": tc.name,
                    "arguments": tc.arguments,
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        tool_calls = None
        if "tool_calls" in data:
            tool_calls = [
                ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
                for tc in data["tool_calls"]
            ]
        return cls(
            type=MessageType(data["type"]),
            content=data["content"],
            tool_calls=tool_calls,
            tool_call_id=data.get("tool_call_id"),
            timestamp=data.get("timestamp", time.time()),
        )


class Session:
    def __init__(self, max_messages: int = 50) -> None:
        self.messages: List[Message] = []
        self.max_messages = max_messages

    def add_message(self, message: Message) -> None:
        self.messages.append(message)
        # Auto-truncate if too many messages
        if len(self.messages) > self.max_messages:
            # Keep first message (usually user) and last N messages
            keep_count = self.max_messages // 2
            self.messages = [self.messages[0]] + self.messages[-(keep_count):]

    def to_chat_messages(self) -> List[ChatMessage]:
        result = []
        for msg in self.messages:
            if msg.type == MessageType.USER:
                result.append(ChatMessage(role="user", content=msg.content))
            elif msg.type == MessageType.ASSISTANT:
                result.append(
                    ChatMessage(
                        role="assistant",
                        content=msg.content or "",
                        tool_calls=msg.tool_calls if msg.tool_calls else None,
                    )
                )
            elif msg.type == MessageType.TOOL_RESULT:
                result.append(
                    ChatMessage(
                        role="tool",
                        content=msg.content or "(tool returned no output)",
                        tool_call_id=msg.tool_call_id,
                    )
                )
            elif msg.type == MessageType.PERMISSION_DENIED:
                result.append(
                    ChatMessage(
                        role="tool",
                        content=f"Permission denied: {msg.content}",
                        tool_call_id=msg.tool_call_id,
                    )
                )
            elif msg.type == MessageType.ERROR:
                result.append(ChatMessage(role="user", content=f"Error: {msg.content or ''}"))
        return result

    def compress(self, max_tokens: int) -> None:
        """Compress session context to fit within max_tokens.

        Strategy:
        1. Always preserve the first message (usually a system prompt).
        2. Always preserve the last user request intact.
        3. For tool results in the middle, truncate to first 200 chars.
        4. If still over limit, keep first message + last user msg + last N messages.
        """
        if len(self.messages) <= 2:
            return

        # Step 1: Truncate long tool results first
        self._truncate_tool_results()

        # Step 2: Estimate tokens and drop middle messages if needed
        estimated_tokens = sum(len(m.content) // 4 for m in self.messages)
        while estimated_tokens > max_tokens and len(self.messages) > 3:
            # Remove the second message (index 1) — keeps first + last N
            self.messages.pop(1)
            estimated_tokens = sum(len(m.content) // 4 for m in self.messages)

    def _truncate_tool_results(self) -> None:
        """Truncate long tool result content to reduce token usage.

        Keeps first 200 characters of each tool result.
        """
        for msg in self.messages:
            if msg.type == MessageType.TOOL_RESULT and len(msg.content) > 400:
                lines = msg.content.split("\n")
                preview_lines = []
                char_count = 0
                for line in lines:
                    if char_count + len(line) > 200:
                        remaining = len(msg.content) - char_count
                        preview_lines.append(f"... [+{remaining} more chars]")
                        break
                    preview_lines.append(line)
                    char_count += len(line) + 1
                msg.content = "\n".join(preview_lines)

    def save_to_file(self, path: str) -> None:
        data = {"messages": [m.to_dict() for m in self.messages]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_from_file(self, path: str) -> None:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.messages = [Message.from_dict(m) for m in data.get("messages", [])]
