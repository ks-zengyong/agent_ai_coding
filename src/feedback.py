"""Self-feedback analysis for validation failures."""
from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class ValidationIssue:
    category: str
    message: str
    fix_hint: str


@dataclass
class ValidationResult:
    success: bool
    issues: List[ValidationIssue] = field(default_factory=list)

    def summary(self) -> str:
        if self.success:
            return "Validation passed."
        return "\n".join(f"[{i.category}] {i.message} → {i.fix_hint}" for i in self.issues)


def validate_snake_python(snake_path: Path) -> ValidationResult:
    issues: List[ValidationIssue] = []

    if not snake_path.exists():
        return ValidationResult(
            success=False,
            issues=[
                ValidationIssue(
                    category="missing_file",
                    message=f"{snake_path} not found",
                    fix_hint="Agent should call write_file to create the game",
                )
            ],
        )

    source = snake_path.read_text(encoding="utf-8")
    try:
        ast.parse(source)
    except SyntaxError as e:
        issues.append(
            ValidationIssue(
                category="syntax_error",
                message=str(e),
                fix_hint="Agent should edit_file to fix syntax",
            )
        )

    if "--self-test" not in source:
        issues.append(
            ValidationIssue(
                category="missing_self_test",
                message="snake.py lacks --self-test mode",
                fix_hint="Add --self-test flag for automated validation",
            )
        )

    if issues:
        return ValidationResult(success=False, issues=issues)

    proc = subprocess.run(
        [sys.executable, str(snake_path), "--self-test"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(snake_path.parent),
    )
    if proc.returncode != 0:
        issues.append(
            ValidationIssue(
                category="runtime_error",
                message=(proc.stderr or proc.stdout or "self-test failed")[:500],
                fix_hint="Agent should read output and fix the game code",
            )
        )
        return ValidationResult(success=False, issues=issues)

    return ValidationResult(success=True)


def suggest_agent_fixes(result: ValidationResult) -> str:
    """Build a follow-up prompt from validation issues."""
    if result.success:
        return ""
    lines = ["The previous attempt failed validation. Please fix:"]
    for issue in result.issues:
        lines.append(f"- {issue.category}: {issue.message}")
    lines.append("Use tools to fix the code and ensure `python snake.py --self-test` passes.")
    return "\n".join(lines)


def capture_run_output(snake_path: Path, out_file: Path) -> None:
    """Save self-test output as deliverable 'screenshot'."""
    proc = subprocess.run(
        [sys.executable, str(snake_path), "--self-test"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(snake_path.parent),
    )
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(
        f"=== Snake Demo Run Output ===\n"
        f"exit_code: {proc.returncode}\n\n"
        f"--- stdout ---\n{proc.stdout}\n\n"
        f"--- stderr ---\n{proc.stderr}\n",
        encoding="utf-8",
    )
