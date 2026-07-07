from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import APERIO_HOME, PROJECT_ROOT, get_extensions_enabled


MAX_COMMAND_BYTES = 128 * 1024
COMMAND_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
SKIP_COPY_NAMES = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


@dataclass(frozen=True)
class ExtensionCommand:
    name: str
    description: str
    source: str
    path: Path
    template: str
    approval_mode: str | None = None
    timeout_seconds: int | None = None


@dataclass(frozen=True)
class ExtensionSkill:
    name: str
    ref: str
    description: str
    source: str
    path: Path


@dataclass(frozen=True)
class RenderedExtensionCommand:
    command: ExtensionCommand
    message: str
    approval_mode: str
    timeout_seconds: int


def project_commands_root() -> Path:
    return PROJECT_ROOT / ".aperio" / "commands"


def user_commands_root() -> Path:
    return APERIO_HOME / "commands"


def project_skills_root() -> Path:
    return PROJECT_ROOT / ".aperio" / "skills"


def user_skills_root() -> Path:
    return APERIO_HOME / "skills"


def discover_extension_commands() -> list[ExtensionCommand]:
    if not get_extensions_enabled():
        return []
    commands: dict[str, ExtensionCommand] = {}
    for source, root in _extension_roots("commands"):
        for path in _iter_markdown_files(root):
            command = _load_command_file(source, root, path)
            if command is None:
                continue
            commands.setdefault(command.name, command)
    return sorted(commands.values(), key=lambda item: (item.name, item.source))


def find_extension_command(name: str) -> ExtensionCommand | None:
    normalized = normalize_command_name(name)
    if normalized is None:
        return None
    for command in discover_extension_commands():
        if command.name == normalized:
            return command
    return None


def render_extension_command(
    command_line: str,
    *,
    approval_mode: str,
    timeout_seconds: int,
) -> RenderedExtensionCommand | None:
    command_name, arguments = split_command_line(command_line)
    command = find_extension_command(command_name)
    if command is None:
        return None

    effective_approval = command.approval_mode or approval_mode
    effective_timeout = command.timeout_seconds or timeout_seconds
    message = _render_template(command, arguments)
    return RenderedExtensionCommand(
        command=command,
        message=message,
        approval_mode=effective_approval,
        timeout_seconds=effective_timeout,
    )


def split_command_line(command_line: str) -> tuple[str, str]:
    stripped = command_line.strip()
    if not stripped:
        return "", ""
    name, sep, rest = stripped.partition(" ")
    return name, rest.strip() if sep else ""


def discover_extension_skills() -> list[ExtensionSkill]:
    if not get_extensions_enabled():
        return []
    skills: dict[str, ExtensionSkill] = {}
    for source, root in _extension_roots("skills"):
        resolved_root = _safe_root(root)
        if resolved_root is None:
            continue
        for skill_file in sorted(resolved_root.rglob("SKILL.md")):
            skill = _extension_skill_from_file(source, resolved_root, skill_file)
            if skill is None:
                continue
            skills.setdefault(skill.ref, skill)
    return sorted(skills.values(), key=lambda item: (item.source, item.ref))


def copy_extension_skills(skills_root: Path) -> list[str]:
    copied_refs: list[str] = []
    if not get_extensions_enabled():
        return copied_refs
    for skill in discover_extension_skills():
        destination = (skills_root / skill.ref).resolve()
        try:
            destination.relative_to(skills_root.resolve())
        except ValueError:
            continue
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill.path, destination, ignore=_ignore_skill_copy)
        if not (destination / "SKILL.md").exists():
            shutil.rmtree(destination, ignore_errors=True)
            continue
        copied_refs.append(skill.ref)
    return copied_refs


def discover_runtime_extension_skill_refs(skills_root: Path) -> list[str]:
    refs: list[str] = []
    root = skills_root.resolve()
    for namespace in ("project", "user"):
        namespace_root = root / namespace
        if not namespace_root.exists():
            continue
        for skill_file in sorted(namespace_root.rglob("SKILL.md")):
            try:
                ref = skill_file.parent.resolve().relative_to(root).as_posix()
            except ValueError:
                continue
            refs.append(ref)
    return refs


def normalize_command_name(name: str) -> str | None:
    raw = (name or "").strip().replace("\\", "/")
    if raw.startswith("/"):
        raw = raw[1:]
    raw = raw.strip("/")
    if not raw:
        return None
    parts = raw.split("/")
    if any(part in {"", ".", ".."} or not COMMAND_NAME_RE.match(part) for part in parts):
        return None
    return "/" + "/".join(part.lower() for part in parts)


def read_skill_metadata(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    metadata, _ = parse_frontmatter(text)
    return {
        key: str(value).strip()
        for key, value in metadata.items()
        if key in {"name", "description"} and str(value).strip()
    }


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()

    metadata: dict[str, str] = {}
    for index, line in enumerate(lines[1:80], start=1):
        if line.strip() == "---":
            body = "\n".join(lines[index + 1 :]).strip()
            return metadata, body
        key, sep, value = line.partition(":")
        if sep:
            metadata[key.strip()] = value.strip().strip("\"'")
    return {}, text.strip()


def _extension_roots(kind: str) -> list[tuple[str, Path]]:
    if kind == "commands":
        return [("project", project_commands_root()), ("user", user_commands_root())]
    if kind == "skills":
        return [("project", project_skills_root()), ("user", user_skills_root())]
    return []


def _iter_markdown_files(root: Path) -> list[Path]:
    resolved = _safe_root(root)
    if resolved is None:
        return []
    return [
        path
        for path in sorted(resolved.rglob("*.md"))
        if path.is_file() and not os.path.islink(path) and _is_relative_to(path.resolve(), resolved)
    ]


def _safe_root(root: Path) -> Path | None:
    try:
        resolved = root.expanduser().resolve()
    except OSError:
        return None
    if not resolved.exists() or not resolved.is_dir():
        return None
    return resolved


def _load_command_file(source: str, root: Path, path: Path) -> ExtensionCommand | None:
    try:
        resolved_root = root.expanduser().resolve()
        resolved_path = path.resolve()
        resolved_path.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    if os.path.islink(path):
        return None
    try:
        if resolved_path.stat().st_size > MAX_COMMAND_BYTES:
            return None
        raw = resolved_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    metadata, body = parse_frontmatter(raw)
    name = normalize_command_name(metadata.get("name", "") or _command_name_from_path(resolved_root, resolved_path))
    if name is None or not body:
        return None
    approval_mode = _approval_mode(metadata.get("approval_mode", ""))
    timeout_seconds = _positive_int(metadata.get("timeout_seconds", ""))
    return ExtensionCommand(
        name=name,
        description=str(metadata.get("description", "")).strip(),
        source=source,
        path=resolved_path,
        template=body,
        approval_mode=approval_mode,
        timeout_seconds=timeout_seconds,
    )


def _command_name_from_path(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    return "/" + relative.as_posix()


def _extension_skill_from_file(source: str, root: Path, skill_file: Path) -> ExtensionSkill | None:
    if os.path.islink(skill_file) or os.path.islink(skill_file.parent):
        return None
    try:
        skill_dir = skill_file.parent.resolve()
        skill_dir.relative_to(root)
    except (OSError, ValueError):
        return None

    metadata = read_skill_metadata(skill_file)
    rel_dir = skill_dir.relative_to(root).as_posix()
    ref_suffix = _safe_skill_ref_suffix(rel_dir, metadata.get("name") or skill_dir.name)
    if ref_suffix is None:
        return None
    return ExtensionSkill(
        name=metadata.get("name") or skill_dir.name,
        ref=f"{source}/{ref_suffix}",
        description=metadata.get("description", ""),
        source=source,
        path=skill_dir,
    )


def _safe_skill_ref_suffix(rel_dir: str, fallback_name: str) -> str | None:
    if rel_dir == ".":
        name = _safe_ref_part(fallback_name)
        return name or None
    parts = [_safe_ref_part(part) for part in rel_dir.replace("\\", "/").split("/")]
    if any(not part for part in parts):
        return None
    return "/".join(parts)


def _safe_ref_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value).strip())
    return safe.strip(".-_/").lower()


def _render_template(command: ExtensionCommand, arguments: str) -> str:
    message = command.template
    if "{{args}}" in message or "$ARGUMENTS" in message:
        return message.replace("{{args}}", arguments).replace("$ARGUMENTS", arguments).strip()
    if not arguments:
        return message.strip()
    return f"{message.strip()}\n\nUser arguments:\n{arguments}"


def _approval_mode(value: str) -> str | None:
    mode = str(value or "").strip().lower()
    return mode if mode in {"prompt", "approve", "reject"} else None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _ignore_skill_copy(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    base = Path(directory)
    for name in names:
        if name in SKIP_COPY_NAMES:
            ignored.add(name)
            continue
        candidate = base / name
        if os.path.islink(candidate):
            ignored.add(name)
    return ignored


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
