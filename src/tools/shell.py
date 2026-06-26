from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Dict

from .registry import BaseTool, ToolResult
from .toolchain import find_vcvars64, wrap_msvc_command


DANGEROUS_PATTERNS = [
    r"rm\s+-rf?\s+/",
    r"rm\s+-rf?\s+~",
    r"rm\s+-rf?\s+\*",
    r"mkfs",
    r"dd\s+if=",
    r":\(\)\{ :\|:& \};:",
    r"chmod\s+-R\s+777\s+/",
    r"chown\s+-R\s+",
    r">\s*/dev/sda",
    r"mv\s+.*\s+/dev/null",
    r"wget\s+.*\s+\|\s*bash",
    r"curl\s+.*\s+\|\s*bash",
]


@dataclass
class CommandOutput:
    stdout: str
    stderr: str
    return_code: int


def format_command_output(stdout: str, stderr: str, return_code: int) -> str:
    """Structured output so empty stdout is visible to the model."""
    lines = [f"exit_code: {return_code}"]
    lines.append(f"stdout: {stdout if stdout.strip() else '(empty)'}")
    if stderr.strip():
        lines.append(f"stderr: {stderr}")
    if return_code != 0:
        if not stdout.strip() and not stderr.strip():
            lines.append(
                "hint: command failed with no output — tool may not be in PATH. "
                "On Windows for MSVC use use_msvc_env=true or detect_build_toolchain first."
            )
    elif not stdout.strip() and not stderr.strip():
        lines.append("hint: command succeeded but produced no output")
    return "\n".join(lines)


class ExecuteCommandTool(BaseTool):
    name: str = "execute_command"
    description: str = (
        "Execute a shell command and return exit_code, stdout, stderr. "
        "On Windows for MSVC/CMake builds set use_msvc_env=true to load vcvars64. "
        "Use detect_build_toolchain first to probe available compilers."
    )
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute",
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default: 120)",
                "default": 120,
            },
            "use_msvc_env": {
                "type": "boolean",
                "description": (
                    "Windows only: run command inside MSVC vcvars64 environment "
                    "(required for cl, and often for cmake with MSVC generator)"
                ),
                "default": False,
            },
        },
        "required": ["command"],
    }
    category: str = "shell"

    def _is_dangerous(self, command: str) -> bool:
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return True
        return False

    async def execute(
        self,
        command: str,
        timeout: int = 120,
        use_msvc_env: bool = False,
        **kwargs,
    ) -> ToolResult:
        if self._is_dangerous(command):
            return ToolResult(
                success=False,
                error=f"Command rejected: potentially dangerous command '{command}'",
            )

        run_command = command
        if use_msvc_env:
            vcvars = find_vcvars64()
            if not vcvars:
                return ToolResult(
                    success=False,
                    error=(
                        "use_msvc_env=true but vcvars64.bat not found. "
                        "Install Visual Studio Build Tools (C++ workload) or run detect_build_toolchain."
                    ),
                )
            run_command = wrap_msvc_command(command, vcvars)

        try:
            process = await asyncio.create_subprocess_shell(
                run_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return ToolResult(
                    success=False,
                    error=f"Command timed out after {timeout} seconds",
                )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            return_code = process.returncode if process.returncode is not None else -1

            formatted = format_command_output(stdout, stderr, return_code)
            return ToolResult(
                success=return_code == 0,
                output=formatted,
                error=formatted if return_code != 0 else "",
                data={
                    "stdout": stdout,
                    "stderr": stderr,
                    "return_code": return_code,
                    "command": command,
                    "use_msvc_env": use_msvc_env,
                },
            )

        except Exception as e:
            return ToolResult(success=False, error=str(e))


class DetectBuildToolchainTool(BaseTool):
    name: str = "detect_build_toolchain"
    description: str = (
        "Detect CMake, MSVC (vcvars), and other build tools. "
        "Call this before C++/CMake tasks on Windows instead of guessing paths."
    )
    parameters: Dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    category: str = "readonly"

    async def execute(self, **kwargs) -> ToolResult:
        from .toolchain import detect_toolchain_info

        loop = asyncio.get_event_loop()
        report = await loop.run_in_executor(None, detect_toolchain_info)
        return ToolResult(success=True, output=report)
