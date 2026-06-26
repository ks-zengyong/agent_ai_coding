"""Windows/Linux build toolchain detection helpers."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def find_vcvars64() -> Optional[Path]:
    """Locate vcvars64.bat via vswhere (Windows MSVC)."""
    if platform.system() != "Windows":
        return None

    pf = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = Path(pf) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.exists():
        return None

    try:
        result = subprocess.run(
            [
                str(vswhere),
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property",
                "installationPath",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    if result.returncode != 0 or not result.stdout.strip():
        return None

    vcvars = Path(result.stdout.strip()) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    return vcvars if vcvars.exists() else None


def wrap_msvc_command(command: str, vcvars: Optional[Path] = None) -> str:
    """Wrap command to run inside MSVC developer environment."""
    vcvars = vcvars or find_vcvars64()
    if not vcvars:
        return command
    vc = str(vcvars)
    return f'cmd /c "call "{vc}" && {command}"'


def _run_capture(command: str, timeout: int = 15) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {timeout}s"
    except OSError as e:
        return -1, "", str(e)


def detect_toolchain_info() -> str:
    """Build a human-readable toolchain report for the agent."""
    lines = [f"platform: {platform.system()} {platform.release()}"]

    cmake = shutil.which("cmake")
    lines.append(f"cmake in PATH: {cmake or 'not found'}")
    if cmake:
        code, out, err = _run_capture("cmake --version")
        lines.append(f"cmake --version (exit {code}):")
        lines.append(out.strip() or err.strip() or "(no output)")

    ninja = shutil.which("ninja")
    lines.append(f"ninja in PATH: {ninja or 'not found'}")

    if platform.system() == "Windows":
        cl = shutil.which("cl")
        lines.append(f"cl in PATH (no vcvars): {cl or 'not found'}")

        vcvars = find_vcvars64()
        lines.append(f"vcvars64.bat: {vcvars or 'not found'}")

        if vcvars:
            code, out, err = _run_capture(wrap_msvc_command("cl 2>&1"))
            text = (out + err).strip()
            lines.append(f"cl via vcvars (exit {code}):")
            lines.append(text[:500] if text else "(no output)")

            code, out, err = _run_capture(wrap_msvc_command("cmake --version"))
            text = (out + err).strip()
            lines.append(f"cmake via vcvars (exit {code}):")
            lines.append(text[:300] if text else "(no output)")

        lines.append("")
        lines.append("MSVC + CMake recommended workflow:")
        lines.append("  1. detect_build_toolchain (this tool)")
        lines.append("  2. write CMakeLists.txt + sources")
        if vcvars:
            lines.append(
                '  3. execute_command with use_msvc_env=true, e.g. '
                'cmake -B build -G "Visual Studio 17 2022" -A x64'
            )
            lines.append("  4. execute_command use_msvc_env=true: cmake --build build --config Release")
        else:
            lines.append("  3. Install Visual Studio Build Tools with C++ workload, then retry")
    else:
        gxx = shutil.which("g++")
        lines.append(f"g++ in PATH: {gxx or 'not found'}")
        if gxx:
            code, out, err = _run_capture("g++ --version")
            lines.append((out or err).strip()[:300] or "(no output)")

    return "\n".join(lines)
