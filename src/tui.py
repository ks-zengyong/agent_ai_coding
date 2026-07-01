"""TUI interface using rich."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax

from src.agent import AgentState, CodingAgent
from src.context import Message, MessageType
from src.llm.base import StreamEvent, StreamEventType
from src.llm.model_registry import ModelRegistry
from src.permissions import PermissionGuard
from src.tui_format import (
    format_diff_result,
    format_tool_args,
    format_usage_brief,
    looks_like_markdown,
    thinking_preview,
)

console = Console()
stderr_console = Console(file=sys.stderr, force_terminal=True)

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_CLEAR_LINE = "\r" + " " * 120 + "\r"


class _Spinner:
    """Async spinner writing plain text to stderr (no Rich markup)."""

    def __init__(self) -> None:
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._label = ""

    async def start(self, label: str) -> None:
        await self.stop()
        self._label = label
        self._running = True
        self._task = asyncio.create_task(self._spin())

    async def _spin(self) -> None:
        frame_idx = 0
        while self._running:
            frame = SPINNER_FRAMES[frame_idx % len(SPINNER_FRAMES)]
            stderr_console.print(f"  {frame} {self._label}", end="\r")
            frame_idx += 1
            await asyncio.sleep(0.1)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        stderr_console.print(_CLEAR_LINE, end="")


class PermissionDialog:
    @staticmethod
    def ask(tool_name: str, arguments: dict, permission_guard: PermissionGuard | None = None) -> tuple[bool, str]:
        """Show permission dialog with three options.

        Returns (allowed, action) where action is 'deny', 'allow_once', or 'allow_whitelist'.
        """
        from src.permissions import PermissionDialogResult

        args_str = json.dumps(arguments, indent=2, ensure_ascii=False)
        console.print()
        console.print(Panel(
            f"[bold yellow]Permission Required[/bold yellow]\n\n"
            f"Tool: [cyan]{tool_name}[/cyan]\n\n"
            f"Arguments:\n{args_str}",
            title="Permission Request",
            border_style="yellow",
        ))

        # 对于 shell 命令，显示命令分类
        command = arguments.get("command", "") if isinstance(arguments, dict) else ""
        cmd_class = ""
        if command and permission_guard:
            cmd_class = permission_guard.classify_command(command)

        extra_hint = ""
        if cmd_class == "dangerous":
            extra_hint = " [red](dangerous)[/red]"
        elif cmd_class == "safe":
            extra_hint = " [green](safe)[/green]"

        while True:
            prompt = f"[yellow]Allow (a) / Allow + whitelist (w) / Deny (d)?{extra_hint} [/yellow]"
            response = console.input(prompt).strip().lower()
            if response in ("a", "allow", "y", "yes"):
                return True, PermissionDialogResult.ALLOW_ONCE.value
            if response in ("w", "wl", "whitelist"):
                return True, PermissionDialogResult.ALLOW_AND_WHITELIST.value
            if response in ("d", "deny", "n", "no"):
                return False, PermissionDialogResult.DENY.value


class TUICodingAgent:
    def __init__(
        self,
        agent: CodingAgent,
        permission_guard: PermissionGuard,
        model_registry: ModelRegistry | None = None,
        tool_registry=None,
        config=None,
    ):
        self.agent = agent
        self.permission_guard = permission_guard
        self.model_registry = model_registry
        self.tool_registry = tool_registry
        self.config = config
        self.running = True
        self._collapsed_results: list[str] = []
        self._collapsed_thinking: list[str] = []
        self._status_visible = True
        self._spinner = _Spinner()
        self._debug_mode = False
        self._last_input_cancel_at: float = 0.0

        tui_cfg = getattr(config, "tui", None) if config else None
        self._show_thinking = getattr(tui_cfg, "show_thinking", "collapsed") if tui_cfg else "collapsed"
        self._render_markdown = getattr(tui_cfg, "render_markdown", True) if tui_cfg else True
        self._tool_preview_limit = getattr(tui_cfg, "tool_result_preview", 500) if tui_cfg else 500

        if config and getattr(config, "log_level", "info") == "debug":
            self._debug_mode = True

    def _current_model_label(self) -> str:
        if self.model_registry:
            profile = self.model_registry.get_active_profile()
            return f"{profile.display_label()} [{profile.provider}]"
        return self.agent.provider.model_name

    def _clear_status_line(self) -> None:
        stderr_console.print(_CLEAR_LINE, end="")

    def _update_status(self, state: AgentState, detail: str = "") -> None:
        if not self._status_visible:
            return
        self._clear_status_line()
        model = self._current_model_label()
        if state == AgentState.IDLE:
            status = f"[Model: {model}] Ready"
        elif state == AgentState.THINKING:
            status = f"[Model: {model}] Thinking..."
        elif state == AgentState.EXECUTING:
            status = f"[Model: {model}] {detail}"
        else:
            status = f"[Model: {model}] {state.value}"
        stderr_console.print(status, end="\r")

    def _terminal_width(self) -> int:
        return max(40, min(console.width or 80, 120))

    def _print_input_rule(self) -> None:
        console.print("[dim]" + "─" * self._terminal_width() + "[/dim]", highlight=False)

    def _status_footer_state_label(self) -> str:
        labels = {
            AgentState.IDLE: "ready",
            AgentState.DONE: "ready",
            AgentState.THINKING: "thinking",
            AgentState.EXECUTING: "executing",
            AgentState.AWAITING_PERMISSION: "permission",
            AgentState.ERROR: "error",
        }
        return labels.get(self.agent.state, self.agent.state.value)

    def _status_footer_plain_text(self) -> str:
        """Plain-text status line for input footer (no Rich markup)."""
        model = self._current_model_label()
        state = self._status_footer_state_label()
        parts = [model, state]
        if self.agent.loop_count:
            parts.append(f"loop {self.agent.loop_count}/{self.agent.max_loop}")
        msgs = len(self.agent.session.messages)
        if msgs:
            parts.append(f"{msgs} msgs")
        if self._debug_mode:
            parts.append("debug")
        return "  ·  ".join(parts)

    def _print_status_footer(self) -> None:
        """输入框下方状态栏（Claude Code 风格）。"""
        left = self._status_footer_plain_text()
        hint = "/help · /models · /perm · /exit"
        more_hint = "more: /status /debug /expand /clear /load"
        console.print(f"  [dim]{left}[/dim]    [dim italic]{hint}[/dim italic]")
        console.print(f"  [dim italic]{more_hint}[/dim italic]")

    def _print_input_bottom_with_status(self) -> None:
        self._print_input_rule()
        self._print_status_footer()

    async def _read_user_input(self) -> str:
        """Framed single-line input: Enter submits, rules rendered via Rich."""
        from src.tui_input import read_framed_input

        return await read_framed_input(
            console=console,
            width=self._terminal_width(),
            status_text=self._status_footer_plain_text,
            hint="/help · /models · /perm · /exit  │ more: /status /debug /expand /clear /load",
        )

    async def _stop_spinner(self) -> None:
        await self._spinner.stop()

    def _print_tool_card(self, tool_name: str, arguments: dict) -> None:
        preview = format_tool_args(arguments)
        if preview:
            console.print(f"  ⏺ [cyan]{tool_name}[/cyan]  {preview}")
        else:
            console.print(f"  ⏺ [cyan]{tool_name}[/cyan]")

    def _print_thinking_block(
        self,
        thinking_text: str,
        elapsed: float,
        usage: dict | None = None,
    ) -> None:
        if self._show_thinking == "hidden":
            return

        usage_str = format_usage_brief(usage)
        meta = f"◆ Thinking ({elapsed:.1f}s"
        if usage_str:
            meta += f", {usage_str}"
        meta += ")"

        if not thinking_text.strip():
            console.print(f"  [dim italic]{meta}[/dim italic]")
            return

        if self._show_thinking == "expanded":
            console.print(f"  [dim italic]{meta}[/dim italic]")
            for line in thinking_text.splitlines():
                console.print(f"    [dim italic]{line}[/dim italic]")
            self._collapsed_thinking.append(thinking_text)
            return

        preview = thinking_preview(thinking_text)
        console.print(f"  [dim italic]{meta}[/dim italic]  {preview}")
        if len(thinking_text) > len(preview):
            self._collapsed_thinking.append(thinking_text)

    def _print_responded_meta(self, elapsed: float, usage: dict | None = None) -> None:
        if self._show_thinking == "hidden":
            return
        usage_str = format_usage_brief(usage)
        meta = f"◆ Responded in {elapsed:.1f}s"
        if usage_str:
            meta += f" ({usage_str})"
        console.print(f"  [dim italic]{meta}[/dim italic]")

    def _render_markdown_block(self, content: str) -> None:
        if not self._render_markdown or not looks_like_markdown(content):
            return
        console.print()
        console.print(Markdown(content))

    def _print_tool_result(self, content: str, tool_name: str = "") -> None:
        if not content:
            console.print("  ⎿  (empty)", style="dim")
            return

        limit = self._tool_preview_limit
        if len(content) <= limit:
            if tool_name == "read_file" and content.count("\n") >= 3:
                ext = "python"
                if "{" in content[:50]:
                    ext = "json"
                console.print("  ⎿ ", style="dim", end="")
                console.print(Syntax(content, ext, theme="monokai", line_numbers=False))
            else:
                for line in content.split("\n"):
                    console.print(f"  ⎿  {line}", style="dim")
            return

        preview = content[:300]
        for line in preview.split("\n"):
            console.print(f"  ⎿  {line}", style="dim")
        remaining = content[300:]
        extra_lines = len(remaining.split("\n"))
        console.print(f"  [dim]⎿  [... +{extra_lines} more lines — /expand to view][/dim]")
        self._collapsed_results.append(content)

    def show_expand(self, args: str = "") -> None:
        args = args.strip()
        total = len(self._collapsed_results) + len(self._collapsed_thinking)
        if total == 0:
            console.print("● [dim]No collapsed content to expand.[/dim]")
            return

        if args.startswith("thinking"):
            self._expand_thinking(args.replace("thinking", "").strip())
            return

        if not self._collapsed_results:
            console.print("● [dim]No collapsed tool results. Use /expand thinking.[/dim]")
            return

        if args == "0":
            for i, content in enumerate(self._collapsed_results, 1):
                console.print(f"▸ [dim]─── Expanded result #{i} ───[/dim]")
                console.print(content)
        elif not args:
            console.print(f"▸ [dim]─── Expanded result #{len(self._collapsed_results)} ───[/dim]")
            console.print(self._collapsed_results[-1])
        else:
            try:
                idx = int(args) - 1
                if idx < 0 or idx >= len(self._collapsed_results):
                    console.print(
                        f"✗ [red]Invalid index: {args}. "
                        f"Use 1-{len(self._collapsed_results)}.[/red]"
                    )
                    return
                console.print(f"▸ [dim]─── Expanded result #{args} ───[/dim]")
                console.print(self._collapsed_results[idx])
            except ValueError:
                console.print(
                    "✗ [red]Invalid argument. Use /expand, /expand N, /expand thinking.[/red]"
                )

    def _expand_thinking(self, args: str) -> None:
        if not self._collapsed_thinking:
            console.print("● [dim]No collapsed thinking blocks.[/dim]")
            return
        if not args:
            console.print("▸ [dim]─── Expanded thinking ───[/dim]")
            console.print(self._collapsed_thinking[-1])
            return
        try:
            idx = int(args) - 1
            console.print(f"▸ [dim]─── Expanded thinking #{idx + 1} ───[/dim]")
            console.print(self._collapsed_thinking[idx])
        except (ValueError, IndexError):
            console.print("✗ [red]Invalid thinking index.[/red]")

    def print_system(self, message: str) -> None:
        self._clear_status_line()
        console.print(f"● [dim]{message}[/dim]")

    def print_error(self, message: str) -> None:
        self._clear_status_line()
        console.print(f"✗ [red]{message}[/red]")

    def show_help(self) -> None:
        console.print("▸ [bold]Available Commands:[/bold]")
        console.print("▸   /help    — Show this help message")
        console.print("▸   /clear   — Clear the conversation")
        console.print("▸   /models  — List models or switch: /models <id|number>")
        console.print("▸   /status  — Show agent status")
        console.print("▸   /debug   — Toggle LLM request/response debug panels")
        console.print("▸   /perm    — Permission settings: /perm, /perm shell <level>, /perm whitelist [add|del]")
        console.print("▸   /expand  — Expand collapsed results: /expand, /expand N, /expand thinking")
        console.print("▸   /load    — Resume the most recent saved session")
        console.print("▸   /exit    — Exit the application")
        console.print()
        console.print("▸ [bold]Usage:[/bold] Type your natural language task and press Enter.")
        console.print("▸ The agent will analyze your request, use tools to explore/modify")
        console.print("▸ your codebase, and stream back the result.")

    def toggle_debug(self) -> None:
        from src import debug_info
        self._debug_mode = not self._debug_mode
        level = "debug" if self._debug_mode else "info"
        debug_info.configure(enabled=True, log_level=level)
        state = "on" if self._debug_mode else "off"
        self.print_system(f"Debug panels {state} (log_level={level}).")

    def show_status(self) -> None:
        console.print(f"▸ State:  {self.agent.state.value}")
        console.print(f"▸ Loop:   {self.agent.loop_count}/{self.agent.max_loop}")
        console.print(f"▸ Model:  {self._current_model_label()}")
        console.print(f"▸ Msgs:   {len(self.agent.session.messages)}")
        console.print(f"▸ Debug:  {'on' if self._debug_mode else 'off'}")
        if self._collapsed_results:
            console.print(f"▸ Collapsed results: {len(self._collapsed_results)}")
        if self._collapsed_thinking:
            console.print(f"▸ Collapsed thinking: {len(self._collapsed_thinking)}")

    def show_models(self, args: str = "") -> None:
        if not self.model_registry:
            console.print(f"▸ [cyan]Current Model:[/cyan] {self.agent.provider.model_name}")
            return

        args = args.strip()
        if not args:
            for line in self.model_registry.list_display().split("\n"):
                if line.strip():
                    console.print(f"▸ {line}")
            return

        try:
            profile = self.model_registry.switch(args)
            if self.tool_registry:
                tools = self.tool_registry.get_tool_definitions()
                self.agent.provider = self.model_registry.apply_tools_to_active(tools)
            else:
                self.agent.provider = self.model_registry.get_active_provider()
            self.print_system(
                f"Switched to [{profile.id}] {profile.display_label()} ({profile.provider})"
            )
        except ValueError as e:
            self.print_error(str(e))

    def load_session(self) -> None:
        """Load the most recent session from the history directory."""
        import glob as glob_mod
        from pathlib import Path

        if not self.config or not self.config.history_dir:
            self.print_error("No history directory configured.")
            return

        history_dir = self.config.history_dir
        if not history_dir.exists():
            self.print_error(f"History directory not found: {history_dir}")
            return

        session_files = sorted(
            history_dir.glob("tui_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not session_files:
            self.print_error("No saved sessions found.")
            return

        latest = session_files[0]
        try:
            self.agent.session.load_from_file(str(latest))
            self.agent.state = AgentState.IDLE
            self.agent.loop_count = 0
            self._collapsed_results = []
            self._collapsed_thinking = []
            msgs = len(self.agent.session.messages)
            self.print_system(f"Resumed session from {latest.name} ({msgs} messages).")
        except Exception as e:
            self.print_error(f"Failed to load session: {e}")

    def show_permissions(self, args: str = "") -> None:
        """Show and manage permission settings."""
        from src.permissions import ShellPermissionLevel

        args = args.strip()

        # /perm 不带参数 → 显示当前状态
        if not args:
            cfg = self.config.permission if self.config else None
            guard = self.permission_guard
            console.print("▸ [bold]Permission Settings[/bold]")
            console.print(f"▸   auto_exec_readonly: {cfg.auto_exec_readonly if cfg else True}")
            console.print(f"▸   confirm_write:     {cfg.confirm_write if cfg else True}")
            console.print(f"▸   confirm_shell:     {cfg.confirm_shell if cfg else True}")
            console.print(f"▸   shell_level:       {cfg.shell_level if cfg else 'auto_safe'}")
            whitelist = sorted(guard._whitelist) if hasattr(guard, '_whitelist') else []
            if whitelist:
                console.print("▸   whitelist:")
                for entry in whitelist:
                    console.print(f"▸     - {entry}")
            else:
                console.print("▸   whitelist: (empty)")
            console.print()
            console.print("▸ [dim]Usage: /perm shell ask_all|auto_safe|skip_all[/dim]")
            console.print("▸ [dim]      /perm whitelist[/dim]")
            return

        parts = args.split()
        subcmd = parts[0].lower()

        if subcmd == "shell" and len(parts) >= 2:
            level = parts[1].lower()
            if level not in ("ask_all", "auto_safe", "skip_all"):
                self.print_error(f"Invalid shell level: {level}. Use ask_all, auto_safe, or skip_all.")
                return
            if self.config and hasattr(self.config, 'permission'):
                self.config.permission.shell_level = level
            self.print_system(f"Shell permission level set to: {level}")
            return

        if subcmd == "whitelist":
            whitelist = sorted(self.permission_guard._whitelist) if hasattr(self.permission_guard, '_whitelist') else []
            if not whitelist:
                console.print("▸ [dim]Whitelist: (empty)[/dim]")
            else:
                console.print("▸ [bold]Whitelist:[/bold]")
                for entry in whitelist:
                    console.print(f"▸   - {entry}")
            if len(parts) >= 3:
                op = parts[1].lower()
                cmd = " ".join(parts[2:])
                if op == "add":
                    self.permission_guard.add_to_whitelist(cmd)
                    self.print_system(f"Added to whitelist: {cmd}")
                elif op == "del" or op == "remove":
                    self.permission_guard._whitelist.discard(cmd.strip().lower())
                    self.print_system(f"Removed from whitelist: {cmd}")
                else:
                    self.print_error(f"Unknown whitelist operation: {op}. Use add or del.")
            return

        self.print_error(f"Unknown /perm subcommand: {subcmd}. See /help for usage.")

    def handle_models_command(self, user_input: str) -> None:
        parts = user_input.split(maxsplit=1)
        selector = parts[1] if len(parts) > 1 else ""
        self.show_models(selector)

    def clear_conversation(self) -> None:
        self.agent.session = type(self.agent.session)()
        self.agent.state = AgentState.IDLE
        self.agent.loop_count = 0
        self._collapsed_results = []
        self._collapsed_thinking = []
        self.print_system("Conversation cleared.")

    async def _run_thinking_stream(self) -> None:
        """Consume step_stream with thinking spinner, tool cards, and text streaming."""
        start_time = time.time()
        is_first_output = True
        is_first_text = True
        accumulated_text = ""
        accumulated_thinking = ""

        await self._spinner.start("Thinking…")

        try:
            async for event in self.agent.step_stream():
                if event.type in (
                    StreamEventType.TEXT_DELTA,
                    StreamEventType.THINKING_DELTA,
                    StreamEventType.TOOL_USE,
                ):
                    if is_first_output:
                        await self._stop_spinner()
                        is_first_output = False

                if event.type == StreamEventType.THINKING_DELTA:
                    accumulated_thinking += event.text

                elif event.type == StreamEventType.TEXT_DELTA:
                    self._status_visible = False
                    accumulated_text += event.text
                    if is_first_text:
                        self._clear_status_line()
                        console.print(highlight=False)
                        console.print("│ ", end="", style="cyan", highlight=False)
                        is_first_text = False
                    console.print(event.text, end="", style="cyan", highlight=False)

                elif event.type == StreamEventType.TOOL_USE and event.tool_call:
                    self._clear_status_line()
                    self._print_tool_card(event.tool_call.name, event.tool_call.arguments)

                elif event.type == StreamEventType.DONE:
                    elapsed = time.time() - start_time
                    if not is_first_text:
                        console.print()
                    if accumulated_thinking.strip():
                        self._print_thinking_block(accumulated_thinking, elapsed, event.usage)
                    elif not is_first_output:
                        self._print_responded_meta(elapsed, event.usage)
                    elif is_first_output:
                        await self._stop_spinner()
                        self._print_responded_meta(elapsed, event.usage)
                    if accumulated_text.strip():
                        self._render_markdown_block(accumulated_text)
                    self._status_visible = True

                elif event.type == StreamEventType.ERROR:
                    await self._stop_spinner()
                    self._clear_status_line()
                    self._status_visible = True
                    console.print(f"✗ [red]{event.text}[/red]")
        finally:
            await self._stop_spinner()

    async def _run_tool_execution(self, tc) -> tuple[float, int]:
        """Execute tool with spinner; return (duration, msg_count_before)."""
        args_preview = format_tool_args(tc.arguments)
        msg_before = len(self.agent.session.messages)
        start_time = time.time()

        await self._spinner.start(f"{tc.name}({args_preview})")
        try:
            async for _ in self.agent.step_stream():
                pass
        finally:
            await self._stop_spinner()

        return time.time() - start_time, msg_before

    async def run_agent_loop(self, user_message: str) -> None:
        from src.agent_runner import build_system_prompt

        messages_before = len(self.agent.session.messages)

        has_system = any(
            m.type == MessageType.USER and m.content.startswith("[System]")
            for m in self.agent.session.messages
        )
        if not has_system:
            identity = (
                self.model_registry.get_active_profile().display_label()
                if self.model_registry else None
            )
            self.agent.session.add_message(
                Message(type=MessageType.USER, content=f"[System]\n{build_system_prompt(identity)}")
            )

        self.agent.session.add_message(Message(type=MessageType.USER, content=user_message))
        self.agent.state = AgentState.IDLE
        self.agent.loop_count = 0
        self.agent._empty_response_retries = 0
        rendered_count = messages_before

        try:
            while True:
                self._update_status(self.agent.state)

                if self.agent.state == AgentState.AWAITING_PERMISSION:
                    if self.agent.pending_tool_call:
                        tc = self.agent.pending_tool_call
                        # 提取命令参数（用于 shell 命令分类和 whitelist）
                        command_arg = tc.arguments.get("command", "") if tc.arguments else ""

                        if self.permission_guard.needs_permission(tc.name, command_arg):
                            allowed, action = PermissionDialog.ask(
                                tc.name, tc.arguments, self.permission_guard
                            )
                            if allowed:
                                self.agent.state = AgentState.EXECUTING
                                if action == "allow_whitelist" and command_arg:
                                    # 提取命令前缀加入白名单
                                    prefix = command_arg.strip().split()[0] if command_arg.strip() else ""
                                    if prefix:
                                        self.permission_guard.add_to_whitelist(prefix)
                                        console.print(
                                            f"  ● [dim]Whitelisted: {prefix}... (auto-approved next time)[/dim]"
                                        )
                            else:
                                self.agent.handle_permission_denied(tc)
                                self._clear_status_line()
                                console.print(
                                    f"  ✗ [dim]Permission denied: "
                                    f"{tc.name}[/dim]"
                                )
                        else:
                            # Config says auto-approve this type — skip dialog
                            self.agent.state = AgentState.EXECUTING
                    continue

                if self.agent.state in (AgentState.DONE, AgentState.ERROR):
                    break

                if self.agent.state in (AgentState.IDLE, AgentState.THINKING):
                    await self._run_thinking_stream()

                    for i in range(rendered_count, len(self.agent.session.messages)):
                        msg = self.agent.session.messages[i]
                        if msg.type == MessageType.ERROR:
                            self.print_error(f"Error: {msg.content}")
                        elif msg.type == MessageType.PERMISSION_DENIED:
                            console.print(f"● [dim]{msg.content}[/dim]")

                    rendered_count = len(self.agent.session.messages)

                    if self.agent.state == AgentState.AWAITING_PERMISSION:
                        continue
                    if self.agent.state in (AgentState.DONE, AgentState.ERROR):
                        break

                if self.agent.state == AgentState.EXECUTING:
                    tc = self.agent.pending_tool_call
                    if tc:
                        duration, msg_before = await self._run_tool_execution(tc)

                        for i in range(msg_before, len(self.agent.session.messages)):
                            msg = self.agent.session.messages[i]
                            if msg.type == MessageType.TOOL_RESULT:
                                diff = format_diff_result(tc.name, tc.arguments)
                                if diff:
                                    console.print(f"  {diff}", style="green")
                                else:
                                    console.print(
                                        f"  ✓ [green]{tc.name}[/green] ({duration:.1f}s)"
                                    )
                                self._print_tool_result(msg.content, tc.name)
                            elif msg.type == MessageType.ERROR:
                                error_summary = msg.content[:80]
                                console.print(
                                    f"  ✗ [red]{tc.name}[/red] ({duration:.1f}s) — "
                                    f"{error_summary}"
                                )
                                self.print_error(msg.content)
                            elif msg.type == MessageType.PERMISSION_DENIED:
                                console.print(f"● [dim]{msg.content}[/dim]")

                        rendered_count = len(self.agent.session.messages)

                    if self.agent.state in (AgentState.DONE, AgentState.ERROR):
                        break

            self._clear_status_line()
            self._status_visible = True
            if self.agent.state == AgentState.DONE:
                self.print_system("Agent finished.")
            elif self.agent.state == AgentState.ERROR:
                self.print_error("Agent encountered an error.")
        except Exception as e:
            await self._stop_spinner()
            self._clear_status_line()
            self._status_visible = True
            self.print_error(f"Agent error: {e}")
            self.agent.state = AgentState.ERROR

    async def run_interactive(self) -> None:
        model_name = self._current_model_label()
        tips = [
            ("Tips for getting started", ""),
            ("/help", "Show all commands"),
            ("/models", "Switch models"),
            ("/perm", "Permission settings"),
            ("/debug", "Toggle debug panels"),
            ("/clear", "Clear conversation"),
            ("/status", "Show agent status"),
            ("/expand", "Expand collapsed results"),
            ("", ""),
            (f"Model: {model_name}", ""),
        ]
        tip_lines = []
        for i, (k, v) in enumerate(tips):
            if i == 0:
                tip_lines.append(f"  [bold]{k}[/bold]")
            elif not k:
                tip_lines.append("")
            else:
                tip_lines.append(f"  [dim]{k}[/dim]  {v}")

        left = [
            "",
            "         [bold cyan]Welcome![/bold cyan]",
            "",
            "         ▐▛███▜▌",
            "        ▝▜█████▛▘",
            "          ▘▘ ▝▝",
            "",
        ]
        from rich.table import Table
        grid = Table.grid(padding=(0, 2))
        grid.add_column(justify="left", width=30)
        grid.add_column(justify="left", width=35)
        for i in range(max(len(left), len(tip_lines))):
            l = left[i] if i < len(left) else ""
            r = tip_lines[i] if i < len(tip_lines) else ""
            grid.add_row(l, r)

        console.print(Panel(
            grid,
            title="TUI Coding Agent v0.1.0",
            border_style="cyan",
            padding=(0, 1),
        ))
        console.print()

        while self.running:
            try:
                user_input = await self._read_user_input()
                self._last_input_cancel_at = 0.0
            except EOFError:
                console.print()
                self.print_system("Goodbye!")
                self.running = False
                break
            except Exception as exc:
                from src.tui_input import InputCancelled, InputExitRequested

                if isinstance(exc, InputExitRequested):
                    console.print()
                    self.print_system("Goodbye!")
                    self.running = False
                    break
                if isinstance(exc, InputCancelled):
                    now = time.monotonic()
                    from src.tui_input import DOUBLE_CTRL_C_SECONDS

                    if (
                        self._last_input_cancel_at
                        and (now - self._last_input_cancel_at) <= DOUBLE_CTRL_C_SECONDS
                    ):
                        console.print()
                        self.print_system("Goodbye!")
                        self.running = False
                        break
                    self._last_input_cancel_at = now
                    console.print()
                    self.print_system("Input cancelled. Press Ctrl+C again to exit.")
                    continue
                raise

            user_input = user_input.strip()
            if not user_input:
                continue

            if user_input.startswith("/"):
                cmd = user_input.split()[0].lower()
                if cmd == "/help":
                    self.show_help()
                elif cmd == "/clear":
                    self.clear_conversation()
                elif cmd == "/models":
                    self.handle_models_command(user_input)
                elif cmd == "/status":
                    self.show_status()
                elif cmd == "/debug":
                    self.toggle_debug()
                elif cmd == "/expand":
                    parts = user_input.split(maxsplit=1)
                    self.show_expand(parts[1] if len(parts) > 1 else "")
                elif cmd == "/load":
                    self.load_session()
                elif cmd == "/perm":
                    parts = user_input.split(maxsplit=2)
                    self.show_permissions(parts[1] if len(parts) > 1 else "")
                elif cmd == "/exit":
                    self.print_system("Goodbye!")
                    self.running = False
                else:
                    self.print_system(f"Unknown command: {cmd}. Type /help for available commands.")
                continue

            await self.run_agent_loop(user_input)


async def run_tui(agent, permission_guard, config=None, model_registry=None, tool_registry=None):
    tui = TUICodingAgent(agent, permission_guard, model_registry, tool_registry, config)
    await tui.run_interactive()
