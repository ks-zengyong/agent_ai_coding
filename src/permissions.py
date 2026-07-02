from __future__ import annotations

import re
from enum import Enum
from typing import Dict, List, Optional, Set

from src.config import PermissionConfig


class PermissionType(Enum):
    READ_ONLY = "read_only"
    WRITE = "write"
    SHELL = "shell"


class ShellPermissionLevel(str, Enum):
    ASK_ALL = "ask_all"          # 所有 shell 命令都询问
    AUTO_SAFE = "auto_safe"      # 安全命令自动通过，危险命令询问
    SKIP_ALL = "skip_all"        # 所有命令自动通过（含危险命令）


# 安全命令前缀列表 — 匹配命令开头即可
SAFE_COMMAND_PREFIXES = [
    # 编译/构建
    "cmake", "make ", "ninja", "msbuild", "gcc", "g++", "clang", "cl ",
    "cargo ", "go ", "dotnet ",
    # 测试
    "pytest", "python -m pytest", "npm test", "npm run test",
    "mvn ", "gradle", "sbt ",
    # 浏览/读取
    "ls ", "dir ", "cat ", "type ", "tree ", "find ", "grep ", "rg ", "where ",
    "which ", "echo ", "pwd ", "cd ", "more ", "head ", "tail ",
    # 只读 git
    "git status", "git log", "git diff", "git branch", "git remote",
    "git show", "git blame", "git describe",
    # 包管理（只读/安装模式）
    "pip list", "pip show", "npm list", "npm ls",
]
# 写文件命令前缀
WRITE_COMMAND_PREFIXES = [
    "echo >", "echo >>", "cat >", "cat >>",
    "copy ", "cp ", "xcopy ",
    "write_file", "edit_file",
]
# 危险命令模式
DANGEROUS_PATTERNS = [
    r"rm\s+-rf?\s+/",
    r"rm\s+-rf?\s+~",
    r"rm\s+-rf?\s+\*",
    r"del\s+/[fsq].*\.",
    r"rd\s+/[fsq]",
    r"mkfs",
    r"dd\s+if=",
    r":\(\)\{ :\|:& \};:",
    r"chmod\s+-R\s+777\s+/",
    r"chown\s+-R\s+",
    r">\s*/dev/sda",
    r"mv\s+.*\s+/dev/null",
    r"wget\s+.*\s+\|\s*bash",
    r"curl\s+.*\s+\|\s*bash",
    r"git push --force",
    r"git reset --hard",
    r"git clean -[fdx]",
]


def classify_shell_command(command: str) -> str:
    """Classify a shell command into 'safe', 'write', or 'dangerous'.

    'safe' — 只读/测试/编译等安全操作
    'write' — 写入文件但非破坏性
    'dangerous' — 可能造成破坏的操作
    """
    command_stripped = command.strip()

    # 先检查危险模式
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, command_stripped, re.IGNORECASE):
            return "dangerous"

    # 再检查写文件前缀
    for prefix in WRITE_COMMAND_PREFIXES:
        if command_stripped.startswith(prefix):
            return "write"

    # 最后检查安全前缀
    for prefix in SAFE_COMMAND_PREFIXES:
        if command_stripped.startswith(prefix):
            return "safe"

    # 默认：未知命令按 write 级别处理（需要确认）
    return "write"


class PermissionDialogResult(Enum):
    DENY = "deny"
    ALLOW_ONCE = "allow_once"
    ALLOW_AND_WHITELIST = "allow_and_whitelist"


class PermissionGuard:
    def __init__(self, config: Optional[PermissionConfig] = None) -> None:
        self._config = config or PermissionConfig()
        self._tool_permissions: Dict[str, PermissionType] = {
            "read_file": PermissionType.READ_ONLY,
            "list_directory": PermissionType.READ_ONLY,
            "glob": PermissionType.READ_ONLY,
            "search_code": PermissionType.READ_ONLY,
            "detect_build_toolchain": PermissionType.READ_ONLY,
            "write_file": PermissionType.WRITE,
            "edit_file": PermissionType.WRITE,
            "execute_command": PermissionType.SHELL,
        }
        # 全局白名单（来自 config，持久生效，跨 session 共享）
        self._global_whitelist: Set[str] = set()
        for entry in self._config.shell_whitelist:
            self._global_whitelist.add(entry.strip().lower())
        # 会话级白名单（当前对话 session 中用户同意过的命令，会话结束清除）
        # 用于记忆历史同意，避免同一 session 内重复询问相同命令
        self._session_whitelist: Set[str] = set()

    def register_tool(self, tool_name: str, permission_type: PermissionType) -> None:
        self._tool_permissions[tool_name] = permission_type

    def check_permission(self, tool_name: str) -> PermissionType | None:
        return self._tool_permissions.get(tool_name)

    def is_read_only(self, tool_name: str) -> bool:
        perm = self._tool_permissions.get(tool_name)
        return perm == PermissionType.READ_ONLY

    def has_permission(self, tool_name: str, required: PermissionType) -> bool:
        perm = self._tool_permissions.get(tool_name)
        return perm == required

    @staticmethod
    def extract_command_signature(command: str) -> str:
        """提取命令的"签名"用于白名单匹配。

        相比只取第一个 token，这里提取更合理的前缀：
        - ``git push origin main`` → ``git push``（保留子命令）
        - ``npm run build`` → ``npm run``
        - ``python -m pytest tests/`` → ``python -m pytest``
        - ``cmake -B build`` → ``cmake``（无子命令时只取程序名）
        - ``echo hello`` → ``echo``

        这样 ``git push`` 加入白名单后，不会让 ``git reset --hard`` 自动通过。
        """
        command_stripped = command.strip()
        if not command_stripped:
            return ""
        # 标准化空白
        tokens = command_stripped.split()
        if not tokens:
            return ""

        program = tokens[0].lower()

        # 带子命令的程序：保留子命令以避免过度授权
        # 例如 git push 不应让 git reset --hard 自动通过
        subcommand_programs = {
            "git", "npm", "docker", "kubectl", "pip", "cargo",
            "go", "dotnet", "mvn", "gradle", "az", "aws", "gcloud",
        }
        if program in subcommand_programs and len(tokens) >= 2:
            sub = tokens[1].lower()
            # 跳过选项（以 - 开头），只保留真正的子命令
            if not sub.startswith("-"):
                return f"{program} {sub}"

        # python -m pytest → python -m pytest（保留 -m 后的模块名）
        if program in ("python", "python3", "py") and len(tokens) >= 3:
            if tokens[1] == "-m":
                return f"{program} -m {tokens[2].lower()}"

        return program

    def add_to_whitelist(self, command: str) -> None:
        """Add a command to the global whitelist (persists across sessions).

        Deprecated for runtime user approvals — prefer
        :meth:`add_to_session_whitelist` which scopes approvals to the
        current conversation session.
        """
        signature = self.extract_command_signature(command)
        if signature:
            self._global_whitelist.add(signature)

    def add_to_session_whitelist(self, command: str) -> str:
        """Add a command to the session-level whitelist.

        Records the user's approval for the current conversation session so
        that subsequent identical commands do not prompt again. The approval
        is scoped to the command signature (e.g. ``git push``) rather than
        just the program name, preventing over-broad auto-approval.

        Returns the signature that was added (empty string if nothing added).
        """
        signature = self.extract_command_signature(command)
        if signature:
            self._session_whitelist.add(signature)
        return signature

    def clear_session_whitelist(self) -> None:
        """Clear all session-level approvals (e.g. on /clear or new session)."""
        self._session_whitelist.clear()

    def get_global_whitelist(self) -> Set[str]:
        """Return a copy of the global (config-persisted) whitelist."""
        return set(self._global_whitelist)

    def get_session_whitelist(self) -> Set[str]:
        """Return a copy of the session-level whitelist."""
        return set(self._session_whitelist)

    def is_whitelisted(self, command: str) -> bool:
        """Check if a command matches any whitelist entry (prefix match).

        Checks both the global whitelist (from config) and the session
        whitelist (approvals from the current conversation). Matching uses
        the command signature so that ``git push`` in the whitelist does
        not auto-approve ``git reset --hard``.
        """
        signature = self.extract_command_signature(command)
        if not signature:
            return False
        # 精确匹配签名（签名本身已是合理前缀，无需再做 startswith）
        if signature in self._global_whitelist:
            return True
        if signature in self._session_whitelist:
            return True
        # 兼容：旧式全局白名单条目可能存储为任意前缀，做一次 prefix 兜底
        cmd_lower = command.strip().lower()
        for entry in self._global_whitelist:
            if cmd_lower.startswith(entry):
                return True
        return False

    def classify_command(self, command: str) -> str:
        """Classify a shell command string into 'safe', 'write', or 'dangerous'."""
        return classify_shell_command(command)

    def needs_permission(self, tool_name: str, command: str = "") -> bool:
        """Check whether the tool requires user confirmation based on config.

        For SHELL tools, also checks shell_level and command classification.
        Returns True if the tool should prompt the user, False if it can auto-execute.
        """
        perm = self._tool_permissions.get(tool_name)
        if perm is None:
            return True  # unknown tools always require confirmation

        if perm == PermissionType.READ_ONLY:
            return not self._config.auto_exec_readonly

        if perm == PermissionType.WRITE:
            return self._config.confirm_write

        if perm == PermissionType.SHELL:
            # 检查白名单
            if command and self.is_whitelisted(command):
                return False

            if not self._config.confirm_shell:
                return False

            level = self._config.shell_level
            if level == "skip_all":
                return False
            if level == "auto_safe" and command:
                cmd_class = self.classify_command(command)
                if cmd_class == "safe":
                    return False
            # ask_all 或 auto_safe 下的 write/dangerous → 需要询问
            return True

        return True
