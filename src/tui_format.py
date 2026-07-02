"""Pure formatting helpers for TUI display (testable without terminal)."""
from __future__ import annotations


def format_tool_args(arguments: dict) -> str:
    """Summarize tool arguments for single-line display."""
    if not arguments:
        return ""
    first_key = next(iter(arguments))
    first_val = arguments[first_key]
    val_str = str(first_val)
    if len(val_str) > 60:
        val_str = val_str[:57] + "..."
    return val_str


def format_diff_result(tool_name: str, arguments: dict) -> str | None:
    """Diff-style summary for write_file / edit_file."""
    if tool_name == "write_file":
        path = arguments.get("path", "")
        file_content = arguments.get("content", "")
        lines = file_content.count("\n") + 1 if file_content else 0
        return f"✓ Created: {path} (+{lines} lines)"
    if tool_name == "edit_file":
        path = arguments.get("path", "")
        old_text = arguments.get("old_text", "")
        new_text = arguments.get("new_text", "")
        old_lines = old_text.count("\n") + 1
        new_lines = new_text.count("\n") + 1
        return f"✓ Edited: {path} (+{new_lines} -{old_lines} lines)"
    return None


def looks_like_markdown(text: str) -> bool:
    """Heuristic: content likely benefits from Markdown rendering."""
    if not text or len(text) < 20:
        return False
    indicators = ("```", "\n#", "\n##", "**", "\n- ", "\n* ", "\n1. ")
    return any(ind in text for ind in indicators)


def thinking_preview(text: str, max_lines: int = 2) -> str:
    """First N non-empty lines of thinking text for collapsed display."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    preview_lines = lines[:max_lines]
    result = " ".join(preview_lines)
    if len(lines) > max_lines:
        result += " …"
    if len(result) > 120:
        result = result[:117] + "…"
    return result


def format_usage_brief(usage: dict | None) -> str:
    """Compact token usage string for status lines."""
    if not usage:
        return ""
    parts = []
    for key in ("total_tokens", "input_tokens", "output_tokens"):
        if key in usage:
            parts.append(f"{key.replace('_', ' ')}={usage[key]}")
    return ", ".join(parts) if parts else ""


def chunk_tool_result(content: str, max_chars: int = 500) -> list[str]:
    """Split large tool result into logical chunks for display.

    Splits on paragraph boundaries (double newlines) first, then by code blocks
    (```), then by single newlines if a paragraph is still too long.
    Each chunk is <= max_chars. Returns list of chunks.
    """
    if not content or len(content) <= max_chars:
        return [content] if content else []

    chunks: list[str] = []

    # Try splitting on double newlines (paragraph boundaries)
    paragraphs = content.split("\n\n")
    if len(paragraphs) > 1:
        for para in paragraphs:
            if len(para) <= max_chars:
                chunks.append(para)
            else:
                # Paragraph still too long — split by single newlines
                chunks.extend(_split_by_lines(para, max_chars))
        return chunks

    # Try splitting on code block boundaries
    if "```" in content:
        parts = content.split("```")
        for i, part in enumerate(parts):
            if not part.strip():
                continue
            if i > 0 and i < len(parts) - 1:
                part = "```" + part + "```"
            elif i == 0 and content.startswith("```"):
                part = "```" + part
            elif i == len(parts) - 1 and content.endswith("```"):
                part = part + "```"
            if len(part) <= max_chars:
                chunks.append(part)
            else:
                chunks.extend(_split_by_lines(part, max_chars))
        return chunks

    # Fallback: split by lines
    return _split_by_lines(content, max_chars)


def _split_by_lines(text: str, max_chars: int) -> list[str]:
    """Split text into chunks by lines, each chunk <= max_chars."""
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in text.split("\n"):
        line_len = len(line) + 1  # +1 for \n
        if current_len + line_len > max_chars and current:
            chunks.append("\n".join(current))
            current = [line]
            current_len = line_len
        else:
            current.append(line)
            current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks
