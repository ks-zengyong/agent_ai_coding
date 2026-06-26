from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .registry import Tool, ToolResult


def _resolve_safe_path(path: str, base_dir: Optional[str] = None) -> Path:
    base = Path(base_dir or os.getcwd()).resolve()
    target = (base / path).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError(f"Path traversal detected: '{path}' is outside the working directory")
    return target


class ReadFileTool(Tool):
    name: str = "read_file"
    description: str = "Read the contents of a file"
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to read",
            },
        },
        "required": ["path"],
    }
    category: str = "filesystem"

    async def execute(self, path: str, **kwargs) -> ToolResult:
        try:
            safe_path = _resolve_safe_path(path)
            if not safe_path.exists():
                return ToolResult(success=False, error=f"File not found: {path}")
            if not safe_path.is_file():
                return ToolResult(success=False, error=f"Not a file: {path}")
            content = safe_path.read_text(encoding="utf-8", errors="replace")
            return ToolResult(success=True, output=content)
        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to read file: {e}")


class WriteFileTool(Tool):
    name: str = "write_file"
    description: str = "Write content to a file, creating it if it doesn't exist"
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to write",
            },
            "content": {
                "type": "string",
                "description": "Content to write to the file",
            },
        },
        "required": ["path", "content"],
    }
    category: str = "filesystem"

    async def execute(self, path: str, content: str, **kwargs) -> ToolResult:
        try:
            safe_path = _resolve_safe_path(path)
            safe_path.parent.mkdir(parents=True, exist_ok=True)
            safe_path.write_text(content, encoding="utf-8")
            return ToolResult(success=True, output=f"File written successfully: {path}")
        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to write file: {e}")


class EditFileTool(Tool):
    name: str = "edit_file"
    description: str = "Edit a file by replacing old_text with new_text"
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file to edit",
            },
            "old_text": {
                "type": "string",
                "description": "The text to replace",
            },
            "new_text": {
                "type": "string",
                "description": "The text to replace with",
            },
        },
        "required": ["path", "old_text", "new_text"],
    }
    category: str = "filesystem"

    async def execute(self, path: str, old_text: str, new_text: str, **kwargs) -> ToolResult:
        try:
            safe_path = _resolve_safe_path(path)
            if not safe_path.exists():
                return ToolResult(success=False, error=f"File not found: {path}")
            if not safe_path.is_file():
                return ToolResult(success=False, error=f"Not a file: {path}")
            content = safe_path.read_text(encoding="utf-8", errors="replace")
            if old_text not in content:
                return ToolResult(success=False, error=f"old_text not found in file: {path}")
            count = content.count(old_text)
            new_content = content.replace(old_text, new_text)
            safe_path.write_text(new_content, encoding="utf-8")
            return ToolResult(
                success=True,
                output=f"File edited successfully: {path} ({count} replacement{'s' if count != 1 else ''})",
                data={"replacements": count},
            )
        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to edit file: {e}")


class ListDirectoryTool(Tool):
    name: str = "list_directory"
    description: str = "List the contents of a directory"
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the directory to list",
            },
        },
        "required": ["path"],
    }
    category: str = "filesystem"

    async def execute(self, path: str = ".", **kwargs) -> ToolResult:
        try:
            safe_path = _resolve_safe_path(path)
            if not safe_path.exists():
                return ToolResult(success=False, error=f"Directory not found: {path}")
            if not safe_path.is_dir():
                return ToolResult(success=False, error=f"Not a directory: {path}")
            entries = []
            for entry in sorted(safe_path.iterdir()):
                suffix = "/" if entry.is_dir() else ""
                entries.append(f"{entry.name}{suffix}")
            output = "\n".join(entries) if entries else "(empty directory)"
            return ToolResult(success=True, output=output, data={"entries": entries})
        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to list directory: {e}")


class GlobTool(Tool):
    name: str = "glob"
    description: str = "Find files matching a glob pattern"
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern to match files against",
            },
        },
        "required": ["pattern"],
    }
    category: str = "filesystem"

    async def execute(self, pattern: str, **kwargs) -> ToolResult:
        try:
            base = Path(os.getcwd()).resolve()
            matches = list(base.glob(pattern))
            safe_matches = []
            for m in matches:
                resolved = m.resolve()
                if str(resolved).startswith(str(base)):
                    relative = resolved.relative_to(base)
                    suffix = "/" if resolved.is_dir() else ""
                    safe_matches.append(f"{relative}{suffix}")
            safe_matches.sort()
            output = "\n".join(safe_matches) if safe_matches else "(no matches)"
            return ToolResult(success=True, output=output, data={"matches": safe_matches})
        except Exception as e:
            return ToolResult(success=False, error=f"Glob failed: {e}")


class SearchCodeTool(Tool):
    name: str = "search_code"
    description: str = "Search for a pattern in code files"
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "Directory or file path to search in",
                "default": ".",
            },
        },
        "required": ["pattern"],
    }
    category: str = "filesystem"

    async def execute(self, pattern: str, path: str = ".", **kwargs) -> ToolResult:
        try:
            safe_path = _resolve_safe_path(path)
            try:
                regex = re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                return ToolResult(success=False, error=f"Invalid regex pattern: {e}")

            results: List[str] = []

            def _search_file(file_path: Path) -> None:
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                    for line_num, line in enumerate(content.splitlines(), 1):
                        if regex.search(line):
                            relative = file_path.resolve().relative_to(Path(os.getcwd()).resolve())
                            results.append(f"{relative}:{line_num}: {line.strip()}")
                except Exception:
                    pass

            if safe_path.is_file():
                _search_file(safe_path)
            elif safe_path.is_dir():
                for root, dirs, files in os.walk(safe_path):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    for filename in files:
                        if filename.startswith("."):
                            continue
                        file_path = Path(root) / filename
                        _search_file(file_path)
            else:
                return ToolResult(success=False, error=f"Path not found: {path}")

            output = "\n".join(results) if results else "(no matches)"
            return ToolResult(success=True, output=output, data={"matches": results})
        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            return ToolResult(success=False, error=f"Search failed: {e}")
