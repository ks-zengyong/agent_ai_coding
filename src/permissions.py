from __future__ import annotations

from enum import Enum
from typing import Dict


class PermissionType(Enum):
    READ_ONLY = "read_only"
    WRITE = "write"
    SHELL = "shell"


class PermissionGuard:
    def __init__(self) -> None:
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
