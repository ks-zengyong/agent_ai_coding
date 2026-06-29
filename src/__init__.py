"""TUI Coding Agent source package.

在导入任何子模块前，优先将内置 ``_vendor`` 目录加入 ``sys.path``，
使 ``httpx`` / ``rich`` / ``prompt_toolkit`` 等第三方依赖可被直接 import，
无需用户自行 pip install，避免与系统已装版本冲突。
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_VENDOR_DIR = _Path(__file__).parent / "_vendor"
_VENDOR_READY = False

if _VENDOR_DIR.is_dir():
    _marker_names = ("httpx", "rich", "prompt_toolkit", "pygments")
    _has_packages = any((_VENDOR_DIR / name).is_dir() for name in _marker_names)
    if _has_packages:
        _vendor_path = str(_VENDOR_DIR)
        # 移除旧位置后插到最前，确保 vendor 优先于 site-packages
        while _vendor_path in _sys.path:
            _sys.path.remove(_vendor_path)
        _sys.path.insert(0, _vendor_path)
        _VENDOR_READY = True


def vendor_ready() -> bool:
    """Return True when bundled ``src/_vendor`` packages are on sys.path."""
    return _VENDOR_READY


def ensure_vendor_deps() -> None:
    """Exit with instructions when bundled runtime deps are missing."""
    if vendor_ready():
        return
    import sys

    print(
        "缺少运行时依赖：src/_vendor/ 未就绪。\n"
        "请在项目根目录执行：  python scripts/vendor_deps.py",
        file=sys.stderr,
    )
    raise SystemExit(1)
