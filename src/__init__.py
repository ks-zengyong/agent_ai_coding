"""TUI Coding Agent source package.

在导入任何子模块前，优先将内置 ``_vendor`` 目录加入 ``sys.path``，
使 ``httpx`` / ``rich`` 等第三方依赖可被直接 import 而无需 pip 安装。
"""
import sys as _sys
from pathlib import Path as _Path

_VENDOR_DIR = _Path(__file__).parent / "_vendor"
if _VENDOR_DIR.is_dir() and str(_VENDOR_DIR) not in _sys.path:
    _sys.path.insert(0, str(_VENDOR_DIR))
