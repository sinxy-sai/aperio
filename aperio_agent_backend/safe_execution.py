from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT, get_safe_execution_enabled


MAX_OUTPUT = 12000
DEFAULT_TIMEOUT_SECONDS = 20
MAX_TIMEOUT_SECONDS = 120
SHELL_META_PATTERN = re.compile(r"[;&|<>`$]|\r|\n")


READ_ONLY_COMMANDS: dict[str, set[str]] = {
    "git": {"status", "diff", "show", "log", "branch", "rev-parse", "ls-files"},
    "rg": set(),
    "python": {"-m"},
    "py": {"-m"},
}
PYTHON_MODULE_ALLOWLIST = {"py_compile", "compileall"}


@dataclass(frozen=True)
class SafeCommandResult:
    ok: bool
    exit_code: int | None
    command: str
    cwd: str
    stdout: str
    stderr: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "command": self.command,
            "cwd": self.cwd,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "reason": self.reason,
        }


def safe_execution_enabled() -> bool:
    return get_safe_execution_enabled()


def run_safe_command(
    command: str,
    *,
    cwd: str | Path | None = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> SafeCommandResult:
    root = PROJECT_ROOT.resolve()
    workdir = _resolve_cwd(cwd, root)
    command = (command or "").strip()
    if not safe_execution_enabled():
        return _denied(command, workdir, "Safe execution is disabled.")
    if not command:
        return _denied(command, workdir, "Command is empty.")
    if SHELL_META_PATTERN.search(command):
        return _denied(command, workdir, "Shell metacharacters are not allowed.")

    try:
        args = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        return _denied(command, workdir, f"Command parse failed: {exc}")
    if not args:
        return _denied(command, workdir, "Command is empty.")

    allowed, reason = _is_allowed(args)
    if not allowed:
        return _denied(command, workdir, reason)
    executable = shutil.which(args[0])
    if executable is None:
        return _denied(command, workdir, f"Executable not found: {args[0]}")

    timeout = max(1, min(int(timeout_seconds or DEFAULT_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS))
    try:
        completed = subprocess.run(
            [executable, *args[1:]],
            cwd=str(workdir),
            shell=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return SafeCommandResult(
            ok=False,
            exit_code=None,
            command=command,
            cwd=str(workdir),
            stdout=_trim(exc.stdout or ""),
            stderr=_trim(exc.stderr or ""),
            reason=f"Command timed out after {timeout}s.",
        )

    return SafeCommandResult(
        ok=completed.returncode == 0,
        exit_code=completed.returncode,
        command=command,
        cwd=str(workdir),
        stdout=_trim(completed.stdout or ""),
        stderr=_trim(completed.stderr or ""),
    )


def _resolve_cwd(cwd: str | Path | None, root: Path) -> Path:
    if cwd is None or str(cwd).strip() in {"", "."}:
        return root
    candidate = Path(cwd)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("cwd must stay inside APERIO_PROJECT_ROOT") from exc
    return resolved


def _is_allowed(args: list[str]) -> tuple[bool, str]:
    exe = Path(args[0]).name.lower()
    if exe.endswith(".exe"):
        exe = exe[:-4]
    if exe not in READ_ONLY_COMMANDS:
        return False, f"Executable is not allowlisted: {args[0]}"
    allowed_subcommands = READ_ONLY_COMMANDS[exe]
    if not allowed_subcommands:
        return True, ""
    if len(args) < 2:
        return False, f"{exe} requires an allowlisted subcommand."
    subcommand = args[1].lower()
    if subcommand not in allowed_subcommands:
        return False, f"{exe} subcommand is not allowlisted: {args[1]}"
    if exe in {"python", "py"}:
        if len(args) < 3 or args[2] not in PYTHON_MODULE_ALLOWLIST:
            return False, "Only python -m py_compile or python -m compileall are allowed."
    return True, ""


def _denied(command: str, cwd: Path, reason: str) -> SafeCommandResult:
    return SafeCommandResult(
        ok=False,
        exit_code=None,
        command=command,
        cwd=str(cwd),
        stdout="",
        stderr="",
        reason=reason,
    )


def _trim(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    return text[:MAX_OUTPUT] + "\n...[truncated]"
