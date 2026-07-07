from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from deepagents.backends.protocol import (
    EditResult,
    FileData,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)


class AgentSkillSources:
    """Register per-agent read-only skill source directories."""

    virtual_root = "/agent-skills"

    def __init__(self, skills_root: Path) -> None:
        self.skills_root = skills_root.resolve()
        self.sources: dict[str, dict[str, Path]] = {}

    def source(self, agent_name: str, *refs: str) -> list[str]:
        if not refs:
            return []
        source_name = _safe_source_name(agent_name)
        allowed: dict[str, Path] = {}
        for ref in refs:
            src = (self.skills_root / ref.strip("/")).resolve()
            if not (src / "SKILL.md").exists():
                raise FileNotFoundError(f"skill not found or missing SKILL.md: {src}")
            try:
                src.relative_to(self.skills_root)
            except ValueError as exc:
                raise ValueError(f"skill path escapes skills root: {ref}") from exc
            skill_name = src.name
            if skill_name in allowed:
                skill_name = _safe_source_name(ref.strip("/").replace("/", "-"))
            allowed[skill_name] = src
        self.sources[source_name] = allowed
        return [f"{self.virtual_root}/{source_name}"]


class AgentSkillBackend:
    """Read-only virtual backend for per-agent skill isolation."""

    def __init__(self, registry: AgentSkillSources) -> None:
        self.registry = registry

    def ls(self, path: str) -> LsResult:
        parts = self._parts(path)
        if not parts:
            return LsResult(
                entries=[
                    {"path": f"/{source_name}", "is_dir": True}
                    for source_name in sorted(self.registry.sources)
                ],
                error=None,
            )
        if len(parts) == 1:
            source_name = parts[0]
            skills = self.registry.sources.get(source_name)
            if skills is None:
                return LsResult(error=f"skill source not found: {path}", entries=[])
            return LsResult(
                entries=[
                    {"path": f"/{source_name}/{skill_name}", "is_dir": True}
                    for skill_name in sorted(skills)
                ],
                error=None,
            )

        resolved = self._resolve(path)
        if resolved is None or not resolved.exists():
            return LsResult(error=f"path not found: {path}", entries=[])
        if not resolved.is_dir():
            return LsResult(error=f"not a directory: {path}", entries=[])

        source_name, skill_name = parts[0], parts[1]
        entries: list[FileInfo] = []
        for child in sorted(resolved.iterdir(), key=lambda item: item.name):
            entry: FileInfo = {
                "path": self._virtual_path(source_name, skill_name, child),
                "is_dir": child.is_dir(),
            }
            if child.is_file():
                entry["size"] = child.stat().st_size
            entries.append(entry)
        return LsResult(entries=entries, error=None)

    async def als(self, path: str) -> LsResult:
        return self.ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        resolved = self._resolve(file_path)
        if resolved is None or not resolved.is_file():
            return ReadResult(error=f"file not found: {file_path}")
        try:
            content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ReadResult(error=f"file is not utf-8 text: {file_path}")
        lines = content.splitlines(keepends=True)
        if offset:
            lines = lines[offset:]
        if limit is not None:
            lines = lines[:limit]
        return ReadResult(file_data=FileData(content="".join(lines), encoding="utf-8"), error=None)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self.read(file_path, offset=offset, limit=limit)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        matches: list[FileInfo] = []
        for source_name, skill_name, base in self._search_roots(path):
            candidates = [base] if base.is_file() else [item for item in base.rglob("*") if item.is_file()]
            for child in candidates:
                rel_to_base = child.relative_to(base.parent if base.is_file() else base).as_posix()
                rel_to_skill = child.relative_to(self.registry.sources[source_name][skill_name]).as_posix()
                if not _matches_glob(pattern, child.name, rel_to_base, rel_to_skill):
                    continue
                entry: FileInfo = {
                    "path": self._virtual_path(source_name, skill_name, child),
                    "is_dir": child.is_dir(),
                }
                entry["size"] = child.stat().st_size
                matches.append(entry)
                if len(matches) >= 500:
                    return GlobResult(error=None, matches=matches)
        return GlobResult(error=None, matches=matches[:500])

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self.glob(pattern, path=path)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        matches: list[GrepMatch] = []
        for source_name, skill_name, base in self._search_roots(path):
            files = [base] if base.is_file() else [item for item in base.rglob("*") if item.is_file()]
            if glob:
                skill_root = self.registry.sources[source_name][skill_name]
                files = [
                    item
                    for item in files
                    if _matches_glob(glob, item.name, item.relative_to(skill_root).as_posix(), item.as_posix())
                ]

            for file in files:
                try:
                    lines = file.read_text(encoding="utf-8").splitlines()
                except UnicodeDecodeError:
                    continue
                for line_number, text in enumerate(lines, start=1):
                    if pattern not in text:
                        continue
                    matches.append(
                        {
                            "path": self._virtual_path(source_name, skill_name, file),
                            "line": line_number,
                            "text": text,
                        }
                    )
                    if len(matches) >= 500:
                        return GrepResult(error=None, matches=matches)
        return GrepResult(error=None, matches=matches)

    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        return self.grep(pattern, path=path, glob=glob)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            resolved = self._resolve(path)
            if resolved is None or not resolved.is_file():
                responses.append(FileDownloadResponse(path=path, error="file_not_found"))
                continue
            responses.append(FileDownloadResponse(path=path, content=resolved.read_bytes(), error=None))
        return responses

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return self.download_files(paths)

    def write(self, file_path: str, content: str) -> WriteResult:
        return WriteResult(error="agent skill backend is read-only", path=None)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        return self.write(file_path, content)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        return EditResult(error="agent skill backend is read-only", path=None)

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        return self.edit(file_path, old_string, new_string, replace_all=replace_all)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return [FileUploadResponse(path=path, error="agent skill backend is read-only") for path, _ in files]

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        return self.upload_files(files)

    def _parts(self, path: str) -> list[str]:
        normalized = path.replace("\\", "/").strip("/")
        parts = normalized.split("/") if normalized else []
        if parts and parts[0] == "agent-skills":
            parts = parts[1:]
        return parts

    def _resolve(self, path: str) -> Path | None:
        parts = self._parts(path)
        if len(parts) < 2:
            return None
        source_name, skill_name, *rest = parts
        skill_root = self.registry.sources.get(source_name, {}).get(skill_name)
        if skill_root is None:
            return None
        candidate = (skill_root / Path(*rest)).resolve() if rest else skill_root
        try:
            candidate.relative_to(skill_root)
        except ValueError:
            return None
        return candidate

    def _search_roots(self, path: str | None) -> list[tuple[str, str, Path]]:
        parts = self._parts(path or "/")
        roots: list[tuple[str, str, Path]] = []
        if not parts:
            for source_name, skills in self.registry.sources.items():
                roots.extend((source_name, skill_name, skill_root) for skill_name, skill_root in skills.items())
            return roots

        if len(parts) == 1:
            skills = self.registry.sources.get(parts[0], {})
            return [(parts[0], skill_name, skill_root) for skill_name, skill_root in skills.items()]

        resolved = self._resolve("/".join(parts))
        if resolved is None or not resolved.exists():
            return []
        return [(parts[0], parts[1], resolved)]

    def _virtual_path(self, source_name: str, skill_name: str, real_path: Path) -> str:
        skill_root = self.registry.sources[source_name][skill_name]
        rel = real_path.resolve().relative_to(skill_root).as_posix()
        suffix = "" if rel == "." else f"/{rel}"
        return f"/{source_name}/{skill_name}{suffix}"


def _safe_source_name(agent_name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", agent_name.strip())
    return safe.strip("-") or "agent"


def _matches_glob(pattern: str, *candidates: str) -> bool:
    normalized = pattern.lstrip("/")
    return any(fnmatch.fnmatch(candidate, normalized) or fnmatch.fnmatch(candidate, pattern) for candidate in candidates)
