"""Claude-style framed input for TUI Coding Agent.

设计约束（避免来回修复，修改前请对照）:
────────────────────────────────────
1. **Enter = 发送**；单行为主（粘贴可含多行）
2. **输入态必须始终可见**：上横线 → ``> `` 输入 → 下横线 + 状态栏
3. **禁止** 使用 ``PromptSession(multiline=True)`` 或默认 buffer 高度
   （会预占多行或占满光标以下终端区域 → 大量空行）
4. **必须** 使用紧凑 ``HSplit``：各 ``Window`` 设 ``dont_extend_height=True``，
   buffer 高度随 **软换行后的视觉行数** 动态 ``Dimension.exact(n)``，footer 固定为 layout 子节点
   （不依赖 ``renderer_height_is_known``，避免状态栏闪烁/消失）
5. **软换行**：``wrap_lines=True``，行满自动折行，下横线随内容下移；Enter 仍发送
6. **Ctrl+C**：按一次清空当前输入继续编辑；连续两次（≤0.75s）退出程序
7. **Fallback**（无 prompt_toolkit）：Rich ``console.input``，footer 仅在提交后
   显示；Ctrl+C 由 ``tui.py`` 双击检测

历史问题对照:
- 空行 → buffer 被拉伸占满终端
- Enter 换行 → 自定义 Enter 绑定了 insert newline
- 状态栏消失 → 改用 Rich input 后 footer 只在提交后打印
- 回复 ``│`` 前大量空格 → prompt 结束后 stdout 光标仍在行中；需换行复位
- 光标 → 使用系统原生 BLINKING_BEAM（闪烁竖线），不使用软件模拟
"""
from __future__ import annotations

import asyncio
import sys
import time
from typing import Callable, Protocol

_HRULE = "─"
DOUBLE_CTRL_C_SECONDS = 0.75


class InputExitRequested(Exception):
    """连续两次 Ctrl+C：请求退出程序。"""


class InputCancelled(Exception):
    """单次 Ctrl+C（Rich fallback 路径）：取消当前输入行。"""


class _CtrlCTracker:
    """Track double Ctrl+C within a time window."""

    def __init__(self) -> None:
        self._last_at: float = 0.0

    def on_ctrl_c(self) -> str:
        now = time.monotonic()
        if self._last_at and (now - self._last_at) <= DOUBLE_CTRL_C_SECONDS:
            return "exit"
        self._last_at = now
        return "clear"

    def reset(self) -> None:
        self._last_at = 0.0


class _ConsoleLike(Protocol):
    def print(self, *args, **kwargs) -> None: ...

    def input(self, prompt: str = "") -> str: ...


def _hrule(width: int) -> str:
    return _HRULE * max(40, min(width, 120))


def _input_style():
    from prompt_toolkit.styles import Style

    return Style.from_dict({
        "": "bg:default fg:default noreverse",
        "prompt": "bg:default fg:default noreverse",
        "hrule": "bg:default fg:ansibrightblack noreverse",
        "footer": "bg:default fg:ansibrightblack noreverse",
        "hint": "bg:default fg:ansibrightblack italic noreverse",
    })


def _configure_console_cursor(*, visible: bool) -> None:
    """Show/hide Windows console hardware cursor (software │ handles input caret)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        class CONSOLE_CURSOR_INFO(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("bVisible", wintypes.BOOL),
            ]

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        info = CONSOLE_CURSOR_INFO()
        if not kernel32.GetConsoleCursorInfo(handle, ctypes.byref(info)):
            return
        info.bVisible = visible
        if visible:
            info.dwSize = max(1, min(100, 25))
        kernel32.SetConsoleCursorInfo(handle, ctypes.byref(info))
    except Exception:
        pass


def _create_blinking_cursor_output():
    """Create an output with hide_cursor/show_cursor as no-ops.

    prompt_toolkit's renderer calls hide_cursor() then show_cursor() on every
    frame. show_cursor() sends \\x1b[?12l (stop blink) + \\x1b[?25h (show),
    which kills cursor blinking. By making both no-ops, the terminal manages
    cursor visibility and blinking entirely via the BLINKING_BEAM (\\x1b[5 q)
    escape sequence sent once by set_cursor_shape().

    Works on both Vt100_Output (Linux/macOS) and Windows10_Output (Windows 10+).
    Windows10_Output delegates to an inner Vt100_Output via __getattr__, but
    instance attributes take priority over __getattr__ so the override works.
    """
    from prompt_toolkit.output import create_output

    try:
        output = create_output()
    except Exception:
        return None

    # No-op both: let terminal handle cursor visibility & blinking natively.
    # This works for Vt100_Output directly, and for Windows10_Output via
    # __getattr__ delegation (instance attrs override __getattr__).
    try:
        output.hide_cursor = lambda: None
        output.show_cursor = lambda: None
    except (AttributeError, TypeError):
        pass  # read-only attr on some output types — skip override
    return output


def _reset_stdout_cursor() -> None:
    """prompt_toolkit 结束后光标可能停在行中，强制换行避免后续输出前导空格。"""
    sys.stdout.write("\n")
    sys.stdout.flush()


def _build_accept_key_bindings(buffer, tracker: _CtrlCTracker):
    """Enter 发送；Ctrl+C 一次清空、连续两次退出。"""
    from prompt_toolkit.enums import DEFAULT_BUFFER
    from prompt_toolkit.filters import has_focus
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.key_binding.defaults import load_key_bindings
    from prompt_toolkit.key_binding.key_bindings import merge_key_bindings

    accept_kb = KeyBindings()
    focused = has_focus(DEFAULT_BUFFER)

    @accept_kb.add("enter", filter=focused, eager=True)
    def _accept(event) -> None:
        tracker.reset()
        event.app.exit(result=buffer.text, style="class:accepted")

    @accept_kb.add("c-c", filter=focused, eager=True)
    def _interrupt(event) -> None:
        if tracker.on_ctrl_c() == "exit":
            event.app.exit(exception=InputExitRequested(), style="class:aborting")
            return
        buffer.reset()
        event.app.invalidate()

    return merge_key_bindings([load_key_bindings(), accept_kb])


def _estimate_wrapped_lines(text: str, content_width: int) -> int:
    """Estimate visual rows for soft-wrapped input (for tests / height hint)."""
    from prompt_toolkit.utils import get_cwidth

    if content_width < 1:
        content_width = 1
    parts = text.split("\n") if text else [""]
    total = 0
    for line in parts:
        w = get_cwidth(line)
        total += max(1, (w + content_width - 1) // content_width) if w else 1
    return max(1, total)


def _line_prefix(line_number: int, wrap_count: int):
    if line_number == 0 and wrap_count == 0:
        return [("", "> ")]
    return [("", "  ")]


def _build_framed_input_layout(
    *,
    buffer,
    width: int,
    status_text: Callable[[], str],
    hint: str,
):
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension

    w = _hrule(width)
    prompt_cols = 2  # "> "

    buffer_control = BufferControl(
        buffer=buffer,
        focusable=True,
    )

    def top_line() -> FormattedText:
        return FormattedText([("class:hrule", w)])

    def footer_lines() -> FormattedText:
        status = status_text()
        return FormattedText([
            ("class:hrule", w),
            ("", "\n"),
            ("class:footer", status + "    "),
            ("class:hint", hint),
        ])

    def buffer_height() -> Dimension:
        from prompt_toolkit.application.current import get_app

        try:
            term_width = get_app().output.get_size().columns
        except Exception:
            term_width = width
        content_width = max(1, term_width - prompt_cols)
        visual_rows = buffer_control.preferred_height(
            content_width,
            max_available_height=10**6,
            wrap_lines=True,
            get_line_prefix=_line_prefix,
        )
        return Dimension.exact(max(1, visual_rows or 1))

    layout = Layout(
        HSplit([
            Window(
                FormattedTextControl(top_line),
                dont_extend_height=True,
                height=Dimension.exact(1),
            ),
            Window(
                buffer_control,
                height=buffer_height,
                dont_extend_height=True,
                get_line_prefix=_line_prefix,
                wrap_lines=True,
                always_hide_cursor=False,
            ),
            Window(
                FormattedTextControl(footer_lines),
                dont_extend_height=True,
                height=Dimension.exact(2),
            ),
        ])
    )
    return layout, buffer_control


async def _read_framed_input_ptk(
    *,
    width: int,
    status_text: Callable[[], str],
    hint: str,
) -> str:
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.cursor_shapes import CursorShape, SimpleCursorShapeConfig
    from prompt_toolkit.enums import DEFAULT_BUFFER
    from prompt_toolkit.patch_stdout import patch_stdout

    buffer = Buffer(name=DEFAULT_BUFFER)
    tracker = _CtrlCTracker()
    layout, _ = _build_framed_input_layout(
        buffer=buffer,
        width=width,
        status_text=status_text,
        hint=hint,
    )

    def _reflow(_: object) -> None:
        from prompt_toolkit.application.current import get_app

        get_app().invalidate()

    buffer.on_text_changed += _reflow

    # Custom output: override show_cursor to not send \x1b[?12l (stop blink)
    custom_output = _create_blinking_cursor_output()

    app_kwargs = dict(
        layout=layout,
        key_bindings=_build_accept_key_bindings(buffer, tracker),
        style=_input_style(),
        include_default_pygments_style=False,
        full_screen=False,
        erase_when_done=True,
        cursor=SimpleCursorShapeConfig(CursorShape.BLINKING_BEAM),
    )
    if custom_output is not None:
        app_kwargs["output"] = custom_output

    app = Application(**app_kwargs)

    with patch_stdout():
        try:
            result = await app.run_async()
        except InputExitRequested:
            _reset_stdout_cursor()
            raise
        finally:
            _reset_stdout_cursor()
    return result if result is not None else ""


async def _read_framed_input_rich(
    *,
    console: _ConsoleLike,
    width: int,
    status_text: Callable[[], str],
    hint: str,
) -> str:
    """降级：无 prompt_toolkit 时 footer 仅在提交后显示。"""
    w = _hrule(width)
    console.print(f"[dim]{w}[/dim]", highlight=False)
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, lambda: console.input("> "))
    except EOFError:
        raise
    except KeyboardInterrupt:
        raise InputCancelled() from None
    console.print(f"[dim]{w}[/dim]", highlight=False)
    left = status_text()
    console.print(f"  [dim]{left}[/dim]    [dim italic]{hint}[/dim italic]")
    return result or ""


async def read_framed_input(
    *,
    console: _ConsoleLike,
    width: int,
    status_text: Callable[[], str],
    hint: str = "/help · /models · /perm · /exit  │ more: /status /debug /expand /clear /load",
) -> str:
    """Read framed input. Prefer compact prompt_toolkit layout; fallback to Rich."""
    if framed_input_available():
        return await _read_framed_input_ptk(
            width=width,
            status_text=status_text,
            hint=hint,
        )
    return await _read_framed_input_rich(
        console=console,
        width=width,
        status_text=status_text,
        hint=hint,
    )


def framed_input_available() -> bool:
    try:
        import prompt_toolkit  # noqa: F401
        return True
    except ImportError:
        return False
