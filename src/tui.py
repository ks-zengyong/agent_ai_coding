"""TUI interface using rich."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Optional

from rich.console import Console
from rich.panel import Panel

from src.agent import AgentState, CodingAgent
from src.context import Message, MessageType
from src.llm.base import StreamEvent, StreamEventType
from src.llm.model_registry import ModelRegistry
from src.permissions import PermissionGuard

console = Console()

SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_CLEAR_LINE = "\r" + " " * 120 + "\r"  # 兼容所有终端，不依赖 ANSI 转义


class PermissionDialog:
    @staticmethod
    def ask(tool_name: str, arguments: dict) -> bool:
        args_str = json.dumps(arguments, indent=2, ensure_ascii=False)
        console.print()
        console.print(Panel(
            f"[bold yellow]Permission Required[/bold yellow]\n\n"
            f"Tool: [cyan]{tool_name}[/cyan]\n\n"
            f"Arguments:\n{args_str}",
            title="Permission Request",
            border_style="yellow",
        ))
        while True:
            response = console.input("[yellow]Allow/Deny (a/d)? [/yellow]").strip().lower()
            if response in ("a", "allow", "y", "yes"):
                return True
            elif response in ("d", "deny", "n", "no"):
                return False


class TUICodingAgent:
    def __init__(
        self,
        agent: CodingAgent,
        permission_guard: PermissionGuard,
        model_registry: ModelRegistry | None = None,
        tool_registry=None,
    ):
        self.agent = agent
        self.permission_guard = permission_guard
        self.model_registry = model_registry
        self.tool_registry = tool_registry
        self.running = True
        self._collapsed_results: list[str] = []
        self._status_visible = True

    # ── helpers ──────────────────────────────────────────────────────────

    def _current_model_label(self) -> str:
        if self.model_registry:
            profile = self.model_registry.get_active_profile()
            return f"{profile.display_label()} [{profile.provider}]"
        return self.agent.provider.model_name

    def _clear_status_line(self):
        """清除状态栏：回车 + 空格覆盖 + 回车。兼容所有终端。"""
        sys.stderr.write(_CLEAR_LINE)
        sys.stderr.flush()

    def _format_tool_args(self, arguments: dict) -> str:
        """截断工具参数以适配单行显示。"""
        if not arguments:
            return ""
        # 取第一个参数的值作为摘要
        first_key = next(iter(arguments))
        first_val = arguments[first_key]
        val_str = str(first_val)
        if len(val_str) > 60:
            val_str = val_str[:57] + "..."
        return val_str

    # ── status bar ───────────────────────────────────────────────────────

    def _update_status(self, state: AgentState, detail: str = ""):
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
        sys.stderr.write(status)
        sys.stderr.flush()

    # ── spinner ──────────────────────────────────────────────────────────

    async def _run_tool_with_spinner(self, tool_name: str, args_preview: str):
        """在后台旋转动画中执行工具调用，返回 (duration, msg_count_before)。"""
        start_time = time.time()
        msg_before = len(self.agent.session.messages)

        spinner_running = True
        frame_idx = 0

        async def spin():
            nonlocal frame_idx
            while spinner_running:
                frame = SPINNER_FRAMES[frame_idx % len(SPINNER_FRAMES)]
                sys.stderr.write(_CLEAR_LINE + f"  {frame} {tool_name}({args_preview})")
                sys.stderr.flush()
                frame_idx += 1
                await asyncio.sleep(0.1)

        spinner_task = asyncio.create_task(spin())

        try:
            async for _ in self.agent.step_stream():
                pass
        finally:
            spinner_running = False
            await spinner_task

        duration = time.time() - start_time
        return duration, msg_before

    # ── diff-style display ──────────────────────────────────────────────

    def _format_diff_result(self, tool_name: str, arguments: dict) -> str | None:
        """为 write_file / edit_file 生成 diff 风格的摘要行。"""
        if tool_name == "write_file":
            path = arguments.get("path", "")
            file_content = arguments.get("content", "")
            lines = file_content.count("\n") + 1 if file_content else 0
            return f"✓ Created: {path} (+{lines} lines)"
        elif tool_name == "edit_file":
            path = arguments.get("path", "")
            old_text = arguments.get("old_text", "")
            new_text = arguments.get("new_text", "")
            old_lines = old_text.count("\n") + 1
            new_lines = new_text.count("\n") + 1
            return f"✓ Edited: {path} (+{new_lines} -{old_lines} lines)"
        return None

    # ── tool result folding ─────────────────────────────────────────────

    def _print_tool_result(self, content: str):
        if not content:
            console.print("  (empty)", style="dim")
            return

        if len(content) <= 500:
            for line in content.split("\n"):
                console.print(f"  {line}", style="dim")
        else:
            preview = content[:300]
            for line in preview.split("\n"):
                console.print(f"  {line}", style="dim")
            remaining = content[300:]
            extra_lines = len(remaining.split("\n"))
            console.print(f"  [dim][... +{extra_lines} more lines — /expand to view][/dim]")
            self._collapsed_results.append(content)

    def show_expand(self, args: str = ""):
        args = args.strip()
        if not self._collapsed_results:
            console.print("● [dim]No collapsed results to expand.[/dim]")
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
                    "✗ [red]Invalid argument. Use /expand, /expand N, or /expand 0.[/red]"
                )

    # ── simplified output ────────────────────────────────────────────────

    def print_assistant(self, content: str):
        self._clear_status_line()
        # 长文本做滚动折叠：超过 25 行时只显示首尾
        lines = content.split("\n")
        if len(lines) <= 25:
            console.print(f"│ [cyan]{content}[/cyan]")
        else:
            head = "\n".join(lines[:15])
            tail = "\n".join(lines[-5:])
            skipped = len(lines) - 20
            console.print(f"│ [cyan]{head}[/cyan]")
            console.print(f"│ [dim]... ({skipped} lines) ...[/dim]")
            console.print(f"│ [cyan]{tail}[/cyan]")

    def print_system(self, message: str):
        self._clear_status_line()
        console.print(f"● [dim]{message}[/dim]")

    def print_error(self, message: str):
        self._clear_status_line()
        console.print(f"✗ [red]{message}[/red]")

    # ── commands ─────────────────────────────────────────────────────────

    def show_help(self):
        console.print("▸ [bold]Available Commands:[/bold]")
        console.print("▸   /help    — Show this help message")
        console.print("▸   /clear   — Clear the conversation")
        console.print("▸   /models  — List models or switch: /models <id|number>")
        console.print("▸   /status  — Show agent status")
        console.print("▸   /expand  — Expand collapsed tool results: /expand, /expand N, /expand 0")
        console.print("▸   /exit    — Exit the application")
        console.print()
        console.print("▸ [bold]Usage:[/bold] Type your natural language task and press Enter.")
        console.print("▸ The agent will analyze your request, use tools to explore/modify")
        console.print("▸ your codebase, and stream back the result.")

    def show_status(self):
        console.print(f"▸ State:  {self.agent.state.value}")
        console.print(f"▸ Loop:   {self.agent.loop_count}/{self.agent.max_loop}")
        console.print(f"▸ Model:  {self._current_model_label()}")
        console.print(f"▸ Msgs:   {len(self.agent.session.messages)}")
        if self._collapsed_results:
            console.print(f"▸ Collapsed results: {len(self._collapsed_results)}")

    def show_models(self, args: str = ""):
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

    def handle_models_command(self, user_input: str):
        parts = user_input.split(maxsplit=1)
        selector = parts[1] if len(parts) > 1 else ""
        self.show_models(selector)

    def clear_conversation(self):
        self.agent.session = type(self.agent.session)()
        self.agent.state = AgentState.IDLE
        self.agent.loop_count = 0
        self._collapsed_results = []
        self.print_system("Conversation cleared.")

    # ── agent loop ───────────────────────────────────────────────────────

    async def run_agent_loop(self, user_message: str):
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

                # ── permission gate ──────────────────────────────────
                if self.agent.state == AgentState.AWAITING_PERMISSION:
                    if self.agent.pending_tool_call:
                        allowed = PermissionDialog.ask(
                            self.agent.pending_tool_call.name,
                            self.agent.pending_tool_call.arguments,
                        )
                        if allowed:
                            self.agent.state = AgentState.EXECUTING
                        else:
                            self.agent.handle_permission_denied(self.agent.pending_tool_call)
                            self._clear_status_line()
                            console.print(
                                f"  ✗ [dim]Permission denied: "
                                f"{self.agent.pending_tool_call.name}[/dim]"
                            )
                    continue

                if self.agent.state in (AgentState.DONE, AgentState.ERROR):
                    break

                # ── thinking / streaming ─────────────────────────────
                if self.agent.state in (AgentState.IDLE, AgentState.THINKING):
                    is_first_text = True

                    async for event in self.agent.step_stream():
                        if event.type == StreamEventType.TEXT_DELTA:
                            self._status_visible = False
                            if is_first_text:
                                self._clear_status_line()
                                console.print("│ ", end="", style="cyan")
                                is_first_text = False
                            console.print(event.text, end="", style="cyan")
                        elif event.type == StreamEventType.TOOL_USE:
                            # tool calls collected internally by step_stream;
                            # spinner will run during execution phase
                            pass
                        elif event.type == StreamEventType.DONE:
                            if not is_first_text:
                                console.print()  # newline after text stream
                            self._status_visible = True
                        elif event.type == StreamEventType.ERROR:
                            self._clear_status_line()
                            self._status_visible = True
                            console.print(f"✗ [red]{event.text}[/red]")

                    # Render any new non-streaming messages added by this step
                    for i in range(rendered_count, len(self.agent.session.messages)):
                        msg = self.agent.session.messages[i]
                        if msg.type == MessageType.ERROR:
                            self._clear_status_line()
                            console.print(f"✗ [red]Error: {msg.content}[/red]")
                        elif msg.type == MessageType.PERMISSION_DENIED:
                            console.print(f"● [dim]{msg.content}[/dim]")

                    rendered_count = len(self.agent.session.messages)

                    if self.agent.state == AgentState.AWAITING_PERMISSION:
                        continue
                    if self.agent.state in (AgentState.DONE, AgentState.ERROR):
                        break

                # ── tool execution ───────────────────────────────────
                if self.agent.state == AgentState.EXECUTING:
                    tc = self.agent.pending_tool_call
                    if tc:
                        args_preview = self._format_tool_args(tc.arguments)
                        duration, msg_before = await self._run_tool_with_spinner(
                            tc.name, args_preview
                        )

                        # Render tool results
                        for i in range(msg_before, len(self.agent.session.messages)):
                            msg = self.agent.session.messages[i]
                            if msg.type == MessageType.TOOL_RESULT:
                                diff = self._format_diff_result(tc.name, tc.arguments)
                                if diff:
                                    sys.stderr.write(_CLEAR_LINE + f"  {diff}\n")
                                    sys.stderr.flush()
                                else:
                                    sys.stderr.write(
                                        _CLEAR_LINE + f"  ✓ [green]{tc.name}[/green] "
                                        f"({duration:.1f}s)\n"
                                    )
                                    sys.stderr.flush()
                                self._print_tool_result(msg.content)
                            elif msg.type == MessageType.ERROR:
                                error_summary = msg.content[:80]
                                sys.stderr.write(
                                    _CLEAR_LINE + f"  ✗ [red]{tc.name}[/red] "
                                    f"({duration:.1f}s) — {error_summary}\n"
                                )
                                sys.stderr.flush()
                                console.print(f"✗ [red]Error: {msg.content}[/red]")
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
            self._stop_spinner()
            self._clear_status_line()
            self._status_visible = True
            self.print_error(f"Agent error: {e}")
            self.agent.state = AgentState.ERROR

    def _stop_spinner(self):
        """占位 — 用于异常路径中关闭还未完成的动画。"""
        pass

    # ── interactive loop ─────────────────────────────────────────────────

    async def run_interactive(self):
        model_name = self._current_model_label()
        # Claude Code 风格欢迎界面
        tip_w = 35
        tips = [
            ("Tips for getting started", ""),
            ("/help", "Show all commands"),
            ("/models", "Switch models"),
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
        # 用 Rich 的 Table 做左右分栏
        from rich.table import Table
        grid = Table.grid(padding=(0, 2))
        grid.add_column(justify="left", width=30)
        grid.add_column(justify="left", width=tip_w)
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

        while self.running:
            try:
                # 输入区上下各一条分隔线
                sys.stdout.write("─" * 80 + "\n")
                sys.stdout.flush()
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("> ")
                )
                sys.stdout.write("─" * 80 + "\n")
                sys.stdout.flush()
            except (EOFError, KeyboardInterrupt):
                console.print()
                self.print_system("Goodbye!")
                self.running = False
                break

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
                elif cmd == "/expand":
                    parts = user_input.split(maxsplit=1)
                    self.show_expand(parts[1] if len(parts) > 1 else "")
                elif cmd == "/exit":
                    self.print_system("Goodbye!")
                    self.running = False
                else:
                    self.print_system(f"Unknown command: {cmd}. Type /help for available commands.")
                continue

            # Regular message: run agent loop
            await self.run_agent_loop(user_input)


async def run_tui(agent, permission_guard, config=None, model_registry=None, tool_registry=None):
    tui = TUICodingAgent(agent, permission_guard, model_registry, tool_registry)
    await tui.run_interactive()