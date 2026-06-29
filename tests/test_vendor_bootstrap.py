"""Bundled vendor path bootstrap."""
from __future__ import annotations

import src


def test_vendor_ready():
    assert src.vendor_ready() is True


def test_runtime_imports_from_vendor():
    import httpx
    import prompt_toolkit
    import rich

    vendor_root = str(src._VENDOR_DIR)  # noqa: SLF001 — test-only
    assert httpx.__file__.startswith(vendor_root)
    assert rich.__file__.startswith(vendor_root)
    assert prompt_toolkit.__file__.startswith(vendor_root)
