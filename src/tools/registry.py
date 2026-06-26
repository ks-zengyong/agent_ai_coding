from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolResult:
    success: bool
    output: str = ""
    error: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_str(self) -> str:
        if self.data and "return_code" in self.data:
            from src.tools.shell import format_command_output
            return format_command_output(
                self.data.get("stdout", ""),
                self.data.get("stderr", ""),
                self.data["return_code"],
            )
        if self.success:
            return self.output if self.output else "(success, no output)"
        return f"Error: {self.error}"


class Tool(ABC):
    name: str = ""
    description: str = ""
    parameters: Dict[str, Any] = {}
    category: str = "general"

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        ...

    def to_definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


BaseTool = Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def list_tools(self) -> List[Tool]:
        return list(self._tools.values())

    def list_tool_names(self) -> List[str]:
        return list(self._tools.keys())

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return [t.to_definition() for t in self._tools.values()]

    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolResult(
                success=False,
                error=f"Tool '{tool_name}' not found. Available tools: {', '.join(self._tools.keys())}",
            )
        try:
            return await tool.execute(**arguments)
        except Exception as e:
            return ToolResult(success=False, error=str(e))
