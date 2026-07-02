"""Tests for PermissionGuard whitelist semantics and command signature extraction.

Covers the session-vs-global whitelist split and the command-signature-based
matching that prevents over-broad auto-approval (e.g. ``git push`` in the
whitelist must NOT auto-approve ``git reset --hard``).
"""
from __future__ import annotations

from src.config import PermissionConfig
from src.permissions import PermissionGuard


# ---------------------------------------------------------------------------
# extract_command_signature
# ---------------------------------------------------------------------------

def test_signature_simple_program() -> None:
    assert PermissionGuard.extract_command_signature("echo hello") == "echo"


def test_signature_git_with_subcommand() -> None:
    assert PermissionGuard.extract_command_signature("git push origin main") == "git push"


def test_signature_git_reset_kept_separate() -> None:
    assert PermissionGuard.extract_command_signature("git reset --hard HEAD") == "git reset"


def test_signature_npm_run() -> None:
    assert PermissionGuard.extract_command_signature("npm run build") == "npm run"


def test_signature_python_m_module() -> None:
    assert PermissionGuard.extract_command_signature("python -m pytest tests/") == "python -m pytest"


def test_signature_cmake_no_subcommand() -> None:
    assert PermissionGuard.extract_command_signature("cmake -B build -G Ninja") == "cmake"


def test_signature_git_with_option_skips_option() -> None:
    # git --git-dir=x status → still "git status" (option skipped)
    assert PermissionGuard.extract_command_signature("git --git-dir=x status") == "git"


def test_signature_empty() -> None:
    assert PermissionGuard.extract_command_signature("") == ""
    assert PermissionGuard.extract_command_signature("   ") == ""


def test_signature_case_insensitive_program() -> None:
    assert PermissionGuard.extract_command_signature("GIT Push origin") == "git push"


# ---------------------------------------------------------------------------
# Session whitelist
# ---------------------------------------------------------------------------

def test_session_whitelist_starts_empty() -> None:
    guard = PermissionGuard()
    assert guard.get_session_whitelist() == set()
    assert guard.get_global_whitelist() == set()


def test_add_to_session_whitelist_returns_signature() -> None:
    guard = PermissionGuard()
    sig = guard.add_to_session_whitelist("git push origin main")
    assert sig == "git push"
    assert "git push" in guard.get_session_whitelist()


def test_session_whitelist_makes_command_pass() -> None:
    guard = PermissionGuard()
    guard.add_to_session_whitelist("git push origin main")
    assert guard.is_whitelisted("git push origin dev") is True


def test_session_whitelist_does_not_leak_to_other_subcommand() -> None:
    guard = PermissionGuard()
    guard.add_to_session_whitelist("git push origin main")
    # git reset --hard must NOT be auto-approved
    assert guard.is_whitelisted("git reset --hard") is False


def test_clear_session_whitelist() -> None:
    guard = PermissionGuard()
    guard.add_to_session_whitelist("git push")
    guard.add_to_session_whitelist("npm run build")
    assert len(guard.get_session_whitelist()) == 2
    guard.clear_session_whitelist()
    assert guard.get_session_whitelist() == set()
    assert guard.is_whitelisted("git push origin") is False


def test_clear_session_does_not_touch_global() -> None:
    cfg = PermissionConfig(shell_whitelist=["git status"])
    guard = PermissionGuard(cfg)
    guard.add_to_session_whitelist("git push")
    guard.clear_session_whitelist()
    # global whitelist from config survives
    assert "git status" in guard.get_global_whitelist()
    assert guard.is_whitelisted("git status") is True


# ---------------------------------------------------------------------------
# Global whitelist (from config)
# ---------------------------------------------------------------------------

def test_global_whitelist_loaded_from_config() -> None:
    cfg = PermissionConfig(shell_whitelist=["git status", "ls "])
    guard = PermissionGuard(cfg)
    # entries are normalized (stripped + lowercased) on load
    assert "git status" in guard.get_global_whitelist()
    assert "ls" in guard.get_global_whitelist()
    # both should still match their respective commands
    assert guard.is_whitelisted("git status") is True
    assert guard.is_whitelisted("ls -la") is True


def test_global_whitelist_persists_across_clear() -> None:
    cfg = PermissionConfig(shell_whitelist=["git status"])
    guard = PermissionGuard(cfg)
    guard.add_to_session_whitelist("git push")
    guard.clear_session_whitelist()
    assert guard.get_global_whitelist() == {"git status"}
    assert guard.get_session_whitelist() == set()


def test_add_to_whitelist_is_global() -> None:
    guard = PermissionGuard()
    guard.add_to_whitelist("docker build .")
    assert "docker build" in guard.get_global_whitelist()
    assert guard.get_session_whitelist() == set()
    # global whitelist should match
    assert guard.is_whitelisted("docker build -t img .") is True


# ---------------------------------------------------------------------------
# needs_permission interaction with whitelists
# ---------------------------------------------------------------------------

def test_needs_permission_session_whitelist_skips_prompt() -> None:
    cfg = PermissionConfig(
        confirm_shell=True,
        shell_level="ask_all",
    )
    guard = PermissionGuard(cfg)
    # Before whitelist: ask_all → needs permission
    assert guard.needs_permission("execute_command", "git push origin main") is True
    # After session whitelist: no prompt
    guard.add_to_session_whitelist("git push origin main")
    assert guard.needs_permission("execute_command", "git push origin dev") is False


def test_needs_permission_global_whitelist_skips_prompt() -> None:
    cfg = PermissionConfig(
        confirm_shell=True,
        shell_level="ask_all",
        shell_whitelist=["git status"],
    )
    guard = PermissionGuard(cfg)
    assert guard.needs_permission("execute_command", "git status") is False


def test_needs_permission_other_subcommand_still_prompts() -> None:
    cfg = PermissionConfig(
        confirm_shell=True,
        shell_level="ask_all",
    )
    guard = PermissionGuard(cfg)
    guard.add_to_session_whitelist("git push")
    # git reset --hard is a different signature → still prompts
    assert guard.needs_permission("execute_command", "git reset --hard") is True


# ---------------------------------------------------------------------------
# Backward-compat: legacy prefix-style global whitelist entries
# ---------------------------------------------------------------------------

def test_legacy_prefix_global_whitelist_still_matches() -> None:
    """Old-style whitelist entries stored as arbitrary prefixes should still
    match via the prefix fallback in is_whitelisted."""
    cfg = PermissionConfig(shell_whitelist=["mytool"])
    guard = PermissionGuard(cfg)
    # "mytool" is not a known subcommand program, so signature is "mytool"
    # and it should match "mytool --foo bar"
    assert guard.is_whitelisted("mytool --foo bar") is True
