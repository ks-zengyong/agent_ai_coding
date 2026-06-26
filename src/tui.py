"""TUI interface using rich."""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Optional

from rich.console import Console
from rich.panel import Panel

from src.agent import AgentState, CodingAgent
from src.context import MessageType
from src.llm.model_registry import ModelRegistry
from src.permissions import PermissionGuard

console = Console()


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

    def _current_model_label(self) -> str:
        if self.model_registry:
            profile = self.model_registry.get_active_profile()
            return f"{profile.display_label()} [{profile.provider}]"
        return self.agent.provider.model_name

    def print_status(self):
        status = (
            f"[Model: {self._current_model_label()}] "
            f"[State: {self.agent.state.value}] [Loop: {self.agent.loop_count}]"
        )
        # Print to stderr so it doesn't clutter the main output
        sys.stderr.write(f"\r{status}")
        sys.stderr.flush()

    def print_assistant(self, content: str):
        # Print a newline to flush status line
        sys.stderr.write("\n")
        sys.stderr.flush()
        console.print(f"[bold blue]Agent[/bold blue]: {content}")

    def print_user(self, content: str):
        sys.stderr.write("\n")
        sys.stderr.flush()
        console.print(f"[bold green]You[/bold green]: {content}")

    def print_tool_log(self, message: str):
        console.print(f"  [magenta]>[/magenta] {message}")

    def print_system(self, message: str):
        console.print(f"[yellow]System[/yellow]: {message}")

    def print_error(self, message: str):
        console.print(f"[red]Error[/red]: {message}")

    def show_help(self):
        console.print(Panel(
            "[bold]Available Commands:[/bold]\n"
            "  /help    - Show this help message\n"
            "  /clear   - Clear the conversation\n"
            "  /models  - List models or switch: /models <id|number>\n"
            "  /status  - Show agent status\n"
            "  /exit    - Exit the application\n\n"
            "[bold]Usage:[/bold] Type your natural language task and press Enter.\n"
            "The agent will analyze your request, use tools to explore/modify\n"
            "your codebase, and stream back the result.",
            title="Help",
            border_style="cyan",
        ))

    def show_status(self):
        status_text = (
            f"  State:  {self.agent.state.value}\n"
            f"  Loop:   {self.agent.loop_count}/{self.agent.max_loop}\n"
            f"  Model:  {self._current_model_label()}\n"
            f"  Msgs:   {len(self.agent.session.messages)}"
        )
        console.print(Panel(status_text, title="Agent Status", border_style="cyan"))

    def show_models(self, args: str = ""):
        if not self.model_registry:
            console.print(f"[cyan]Current Model:[/cyan] {self.agent.provider.model_name}")
            return

        args = args.strip()
        if not args:
            console.print(Panel(self.model_registry.list_display(), title="Models", border_style="cyan"))
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
        self.print_system("Conversation cleared.")

    async def run_agent_loop(self, user_message: str):
        from src.agent_runner import SYSTEM_PROMPT
        from src.context import Message, MessageType
        messages_before = len(self.agent.session.messages)

        has_system = any(
            m.type == MessageType.USER and m.content.startswith("[System]")
            for m in self.agent.session.messages
        )
        if not has_system:
            self.agent.session.add_message(
                Message(type=MessageType.USER, content=f"[System]\n{SYSTEM_PROMPT}")
            )

        self.agent.session.add_message(Message(type=MessageType.USER, content=user_message))
        self.agent.state = AgentState.IDLE
        self.agent.loop_count = 0
        self.agent._empty_response_retries = 0
        rendered_count = messages_before

        try:
            while True:
                self.print_status()

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
                            self.print_tool_log(f"Permission denied for: {self.agent.pending_tool_call.name}")
                    continue

                should_continue = await self.agent.step()

                # Render only NEW messages added during this agent run
                for i in range(rendered_count, len(self.agent.session.messages)):
                    msg = self.agent.session.messages[i]
                    if msg.type == MessageType.ASSISTANT:
                        if msg.content:
                            self.print_assistant(msg.content)
                        if msg.tool_calls:
                            for tc in msg.tool_calls:
                                args_str = json.dumps(tc.arguments, ensure_ascii=False)
                                self.print_tool_log(f"Calling: {tc.name}({args_str})")
                    elif msg.type == MessageType.TOOL_RESULT:
                        preview = msg.content[:800] if msg.content else "(empty)"
                        self.print_tool_log(f"Result: {preview}")
                    elif msg.type == MessageType.ERROR:
                        self.print_error(msg.content)
                    elif msg.type == MessageType.PERMISSION_DENIED:
                        self.print_system(msg.content)

                rendered_count = len(self.agent.session.messages)

                if not should_continue:
                    break

            sys.stderr.write("\n")
            sys.stderr.flush()
            if self.agent.state == AgentState.DONE:
                self.print_system("Agent finished.")
            elif self.agent.state == AgentState.ERROR:
                self.print_error("Agent encountered an error.")
        except Exception as e:
            sys.stderr.write("\n")
            sys.stderr.flush()
            self.print_error(f"Agent error: {e}")
            self.agent.state = AgentState.ERROR

    async def run_interactive(self):
        console.print(Panel.fit(
            "[bold cyan]TUI Coding Agent[/bold cyan]\n"
            "A terminal coding agent powered by LLM",
            border_style="cyan",
        ))
        console.print()
        self.show_help()
        console.print()

        while self.running:
            try:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: console.input("\n[bold green]>>>[/bold green] ")
                )
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
