"""
Aperio Integrated Demo — task router + two orchestrators with skill-equipped sub-agents.

Architecture:
  Main Router
    ├── code-health-orchestrator  (sync)
    │     ├── architect           (async, skill: code-architect)
    │     ├── security-analyst    (async, skill: code-security)
    │     ├── dependency-checker  (async, skill: code-dependency)
    │     ├── doc-reviewer        (async, skill: code-documentation)
    │     └── summarizer          (sync,  skill: report-writing)
    │
    └── prd-review-orchestrator   (sync)
          ├── product-strategist  (async, skill: review-ops)
          ├── technical-feasibility(async,skill: review-tech)
          ├── ux-researcher       (async, skill: review-ux)
          ├── risk-analyst        (async, skill: review-test)
          └── editor              (sync,  skill: report-writing + review-matrix)

Usage:
  conda activate llm-dev
  python demo/aperio_integrated.py
"""
from __future__ import annotations

import base64
import atexit
import io
import json
import os
import re
import shlex
import sys
import tarfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_DEMO_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DEMO_DIR.parent

from dotenv import load_dotenv
load_dotenv(_DEMO_DIR / ".env")

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import FilesystemBackend, CompositeBackend, StateBackend
from deepagents.backends.protocol import (
    EditResult,
    ExecuteResponse,
    FileData,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)
from deepagents.backends.store import StoreBackend
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from langchain.chat_models import init_chat_model
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_core.tools import tool
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langgraph.config import get_config
from langgraph.types import Command
from deepagents.middleware.skills import _list_skills_with_errors

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WORKSPACE_ROOT = (_PROJECT_ROOT / "demo/workspace_integrated").resolve()
SKILLS_DIR = _DEMO_DIR / "04_skills"
LOCAL_RESOURCES_DIR = _DEMO_DIR / "local_resources"
TARGET_PROJECT = "full-stack-fastapi-template-master/backend"
TARGET_CODE = f"{TARGET_PROJECT}/app/core"
SANDBOX_PROJECT_ROOT = "/workspace/project"
SANDBOX_TARGET_CODE = f"{SANDBOX_PROJECT_ROOT}/app/core"
DEFAULT_SANDBOX_IMAGE = "aperio-sandbox:py311-tools"
SANDBOX_IMAGE = os.environ.get("APERIO_SANDBOX_IMAGE", DEFAULT_SANDBOX_IMAGE)
SANDBOX_DOCKERFILE = (_DEMO_DIR / "sandbox" / "Dockerfile").resolve()
INSTALL_PROJECT_DEPS = os.environ.get("APERIO_INSTALL_PROJECT_DEPS", "0") == "1"


def _skill_dir(name: str) -> str:
    """Build a virtual skill source routed through CompositeBackend."""
    return "/skills/" + name.replace("\\", "/").strip("/")


def _shared_skill_dir() -> str:
    """Return the root shared-skill source under /skills/."""
    return _skill_dir("")


def setup_local_resources() -> Path:
    """Create read-only local resources exposed through CompositeBackend."""
    LOCAL_RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
    policy_file = LOCAL_RESOURCES_DIR / "aperio_policy.yaml"
    if not policy_file.exists():
        policy_file.write_text(
            """security:
  sandbox: docker
  require_human_approval:
    - execute
    - write_file
  internet_search:
    mode: read_only_public_web
    approval_required: false
    evidence_rule: "web snippets are supplemental; local files and tool results remain authoritative"
storage:
  default: docker_sandbox
  outputs: filesystem
  temp: state
  memories: store
  local_resources: read_only_filesystem
output_contract:
  root_markdown_aliases: denied
  final_outputs:
    - /outputs/code_health/code_health_report.md
    - /outputs/prd_review/prd_v2_final.md
    - /outputs/prd_review/review_matrix.md
code_health:
  project_root: /workspace/project
  source_path: /workspace/project/app/core
  draft_dir: /outputs/code_health/drafts
  final_report: /outputs/code_health/code_health_report.md
  toolchain:
    image: aperio-sandbox:py311-tools
    preinstalled:
      - ruff
      - mypy
      - bandit
      - pip-audit
    schema: code-health-tools-v2
    install_project_deps_default: false
    raw_results: /outputs/code_health/raw/tool_results.json
prd_review:
  draft_dir: /outputs/prd_review/drafts
  prd_v1: /outputs/prd_review/prd_v1.md
  final_prd: /outputs/prd_review/prd_v2_final.md
  review_matrix: /outputs/prd_review/review_matrix.md
web_search:
  raw_evidence_dir:
    code_health: /outputs/code_health/raw/web_search
    prd_review: /outputs/prd_review/raw/web_search
  memory_policy: "do not store raw search results in /memories/; only curated reusable conclusions belong there"
team: aperio-demo
""",
            encoding="utf-8",
        )
    return LOCAL_RESOURCES_DIR


def _collect_subagents(subagents: list[dict], parent: str = "root") -> list[tuple[str, dict]]:
    collected: list[tuple[str, dict]] = []
    for spec in subagents:
        name = f"{parent}.{spec['name']}" if parent else spec["name"]
        collected.append((name, spec))
        nested = spec.get("subagents", [])
        if nested:
            collected.extend(_collect_subagents(nested, name))
    return collected


def print_backend_debug(run_root: Path, local_resources: Path, sandbox: "AgentDockerSandbox") -> None:
    print("\n[debug] CompositeBackend routes")
    print(f"  default: DockerSandbox image={sandbox.image} container={sandbox.id[:12] if sandbox.id != 'closed' else 'closed'}")
    print(f"  /outputs/ -> FilesystemBackend({run_root})")
    print("  /memories/ -> StoreBackend(namespace=aperio)")
    print("  /temp/ -> StateBackend()")
    print(f"  /local-resources/ -> FilesystemBackend({local_resources}) [write denied]")
    print(f"  /skills/ -> FilesystemBackend({SKILLS_DIR})")


def print_sandbox_tool_debug(sandbox: "AgentDockerSandbox") -> None:
    print("\n[debug] Sandbox toolchain")
    print(f"  project dependency install: {'enabled' if INSTALL_PROJECT_DEPS else 'disabled'}")
    commands = {
        "python": "python --version",
        "ruff": "ruff --version",
        "mypy": "mypy --version",
        "bandit": "bandit --version",
        "pip-audit": "pip-audit --version",
    }
    for name, command in commands.items():
        output, exit_code = sandbox._sandbox.execute(command)
        first_line = output.strip().splitlines()[0] if output.strip() else ""
        status = "OK" if exit_code == 0 else "MISSING"
        print(f"  {status:7} {name}: {first_line or 'not available'}")


def print_subagent_debug(subagents: list[dict], backend: CompositeBackend) -> None:
    print("\n[debug] Subagents and skills")
    print("  note: custom PerfMiddleware is attached to the main agent only; declared subagents build their own middleware stack.")
    for name, spec in _collect_subagents(subagents):
        skill_sources = spec.get("skills", [])
        print(f"  - {name}: skills={skill_sources or '[]'}")
        for source in skill_sources:
            skills, error = _list_skills_with_errors(backend, source)
            if error:
                print(f"      load ERROR from {source}: {error}")
                continue
            if not skills:
                print(f"      load WARNING from {source}: no SKILL.md found")
                continue
            for skill in skills:
                print(f"      loaded {skill['name']} -> {skill['path']}")


def print_tool_debug(subagents: list[dict], custom_tools: list) -> None:
    custom_tool_names = [getattr(tool_item, "name", getattr(tool_item, "__name__", str(tool_item))) for tool_item in custom_tools]
    print("\n[debug] Tools")
    print("  built-in: write_todos, task, ls, read_file, write_file, edit_file, glob, grep, execute")
    print(f"  custom: {custom_tool_names or '[]'}")
    print("  inheritance: declared subagents without a tools field inherit the main agent tool set.")
    for name, spec in _collect_subagents(subagents):
        if "tools" in spec:
            tool_names = [getattr(tool_item, "name", getattr(tool_item, "__name__", str(tool_item))) for tool_item in spec["tools"]]
            print(f"  - {name}: tools={tool_names} [override]")
        else:
            print(f"  - {name}: tools=inherited + custom={custom_tool_names or '[]'}")


def print_middleware_debug(middleware: list[AgentMiddleware]) -> None:
    print("\n[debug] User-configured middleware")
    print("  note: DeepAgents also installs TodoList, Filesystem, SubAgent, Summarization, and HITL middleware internally.")
    print("  model rate limit: 20 requests/minute shared by primary and fallback models")
    for item in middleware:
        print(f"  - {item.name}")


def print_expected_outputs(run_root: Path) -> None:
    expected = [
        run_root / "code_health" / "code_health_report.md",
        run_root / "prd_review" / "prd_v2_final.md",
        run_root / "prd_review" / "review_matrix.md",
        run_root / "performance.json",
    ]
    print("\n[debug] Expected output files")
    for path in expected:
        status = "OK" if path.exists() and path.stat().st_size > 0 else "MISSING"
        size = path.stat().st_size if path.exists() else 0
        print(f"  {status:7} {path.relative_to(run_root)} ({size} bytes)")


def _is_approval_choice(choice: str) -> bool:
    normalized = re.sub(r"[^a-z]", "", choice.lower())
    return normalized.startswith("a") or normalized == "yes" or normalized == "y"


def handle_human_approval(agent, response, config: dict, label: str):
    print(f"[debug] {label} response_type={type(response).__name__} interrupts={len(getattr(response, 'interrupts', []) or [])}")
    while hasattr(response, "interrupts") and response.interrupts:
        decisions = []
        for interrupt in response.interrupts:
            for action in interrupt.value.get("action_requests", []):
                print(f"\n  ⏸️  HITL: Agent 请求执行 [{action['name']}]")
                if action['name'] == 'execute':
                    command = action['args'].get('command', 'N/A')
                    print(f"     命令: {command}")
                elif action['name'] == 'write_file':
                    file_path = (
                        action['args'].get('file_path')
                        or action['args'].get('path')
                        or action['args'].get('filepath')
                        or action['args'].get('file')
                        or action['args'].get('filename')
                        or action['args'].get('name')
                        or 'N/A'
                    )
                    print(f"     文件: {file_path}")
                    content = action['args'].get('content', '')
                    print(f"     内容: {str(content)[:200]}...")
                choice = input("  [a]pprove / [r]eject: ").strip().lower()
                approved = _is_approval_choice(choice)
                decisions.append({
                    "action_id": action.get("id"),
                    "tool_name": action["name"],
                    "type": "approve" if approved else "reject",
                    "updated_args": action["args"] if approved else None,
                })
        response = agent.invoke(Command(resume={"decisions": decisions}), config=config, version="v2")
        print(f"[debug] {label} resumed_response_type={type(response).__name__} interrupts={len(getattr(response, 'interrupts', []) or [])}")
    return response


# ---------------------------------------------------------------------------
# Docker Sandbox (Exercise 12 pattern)
# ---------------------------------------------------------------------------

class DockerSandbox:
    """DeepAgents-compatible Docker backend based on Exercise 12."""

    def __init__(
        self,
        image: str = SANDBOX_IMAGE,
        working_dir: str = "/workspace",
        remove_on_close: bool = True,
    ):
        try:
            import docker
        except ImportError as exc:
            raise RuntimeError("docker Python package is required for sandbox mode") from exc

        self.image = image
        self.working_dir = working_dir
        self.remove_on_close = remove_on_close
        self.client = docker.from_env()
        self._ensure_image(docker)
        self.container = self.client.containers.run(
            self.image,
            command="tail -f /dev/null",
            detach=True,
            working_dir=self.working_dir,
            remove=self.remove_on_close,
        )
        print(f"Docker sandbox: started {self.container.id[:12]} ({self.image})")

    def _ensure_image(self, docker_module) -> None:
        try:
            self.client.images.get(self.image)
            return
        except docker_module.errors.ImageNotFound:
            if self.image != DEFAULT_SANDBOX_IMAGE:
                raise RuntimeError(
                    f"Docker image not found: {self.image}. "
                    "Build or pull it first, or unset APERIO_SANDBOX_IMAGE to use the demo image."
                )
            if not SANDBOX_DOCKERFILE.exists():
                raise RuntimeError(f"sandbox Dockerfile not found: {SANDBOX_DOCKERFILE}")

            print(f"Docker sandbox image not found: {self.image}")
            print(f"Docker sandbox: building {self.image} from {SANDBOX_DOCKERFILE}")
            print("Docker sandbox: Docker will pull python:3.11-slim if the base image is missing")
            try:
                build_stream = self.client.api.build(
                    path=str(SANDBOX_DOCKERFILE.parent),
                    dockerfile=SANDBOX_DOCKERFILE.name,
                    tag=self.image,
                    pull=True,
                    rm=True,
                    decode=True,
                )
                for event in build_stream:
                    if "error" in event:
                        raise RuntimeError(event["error"])
                    text = event.get("stream", "").strip()
                    if text:
                        print(f"  {text}")
            except Exception as exc:
                raise RuntimeError(f"failed to build sandbox image {self.image}: {exc}") from exc

            self.client.images.get(self.image)
        except docker_module.errors.APIError as exc:
            raise RuntimeError(f"failed to inspect Docker image {self.image}: {exc}") from exc

    def seed_directory(self, source: Path, destination: str) -> None:
        """Copy host directory into the container as demo input."""
        if not source.exists():
            raise FileNotFoundError(f"target code path not found: {source}")

        self.execute(f"rm -rf {shlex.quote(destination)} && mkdir -p {shlex.quote(destination)}")
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as tar:
            for item in source.rglob("*"):
                arcname = str(Path(destination.lstrip("/")) / item.relative_to(source))
                tar.add(item, arcname=arcname, recursive=False)
        stream.seek(0)
        ok = self.container.put_archive("/", stream.getvalue())
        if not ok:
            raise RuntimeError(f"failed to seed sandbox directory: {destination}")

    def execute(self, command: str, timeout: int | None = None) -> tuple[str, int]:
        if not self.container:
            raise RuntimeError("sandbox container is not running")
        exec_command = command
        if timeout is not None:
            exec_command = f"timeout {int(timeout)}s sh -c {shlex.quote(command)}"
        exit_code, output = self.container.exec_run(
            ["sh", "-c", exec_command],
            workdir=self.working_dir,
            demux=False,
        )
        output_str = output.decode("utf-8", errors="replace") if output else ""
        return output_str, exit_code

    def ls(self, path: str = ".") -> LsResult:
        try:
            stdout, exit_code = self.execute(f"ls -1 {shlex.quote(path)}")
            if exit_code != 0:
                return LsResult(error=f"cannot list directory: {path}", entries=[])
            entries = [{"path": f} for f in stdout.strip().split("\n") if f]
            return LsResult(entries=entries, error=None)
        except Exception as exc:
            return LsResult(error=str(exc), entries=[])

    def read(self, path: str, offset: int = 0, limit: Optional[int] = None) -> ReadResult:
        try:
            stdout, exit_code = self.execute(f"cat {shlex.quote(path)}")
            if exit_code != 0:
                return ReadResult(error=f"cannot read file: {path}")
            lines = stdout.splitlines(keepends=True)
            if offset > 0:
                lines = lines[offset:]
            if limit is not None:
                lines = lines[:limit]
            return ReadResult(file_data=FileData(content="".join(lines), encoding="utf-8"), error=None)
        except Exception as exc:
            return ReadResult(error=str(exc))

    def write(self, path: str, content: bytes | str) -> WriteResult:
        if isinstance(content, str):
            content = content.encode("utf-8")
        try:
            dir_path = os.path.dirname(path)
            if dir_path and dir_path != ".":
                self.execute(f"mkdir -p {shlex.quote(dir_path)}")
            content_b64 = base64.b64encode(content).decode("utf-8")
            stdout, exit_code = self.execute(
                f"printf %s {shlex.quote(content_b64)} | base64 -d > {shlex.quote(path)}"
            )
            if exit_code != 0:
                return WriteResult(error=f"write failed: {stdout}", path=None)
            return WriteResult(path=path, error=None)
        except Exception as exc:
            return WriteResult(error=str(exc), path=None)

    def edit(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        instructions: Optional[str] = None,
    ) -> EditResult:
        try:
            read_result = self.read(path)
            if read_result.error:
                return EditResult(path=None, error=read_result.error)
            current = read_result.file_data["content"]
            updated = current.replace(old_string, new_string) if replace_all else current.replace(old_string, new_string, 1)
            if updated == current:
                return EditResult(path=path, error=None)
            write_result = self.write(path, updated)
            if write_result.error:
                return EditResult(path=None, error=write_result.error)
            return EditResult(path=path, error=None)
        except Exception as exc:
            return EditResult(path=None, error=str(exc))

    def glob(self, pattern: str, path: str = ".") -> GlobResult:
        try:
            stdout, exit_code = self.execute(
                f"find {shlex.quote(path)} -name {shlex.quote(pattern)} -type f"
            )
            if exit_code != 0:
                return GlobResult(error=f"glob failed: {stdout}", matches=[])
            return GlobResult(matches=[f for f in stdout.strip().split("\n") if f], error=None)
        except Exception as exc:
            return GlobResult(error=str(exc), matches=[])

    def grep(
        self,
        pattern: str,
        path: str = ".",
        recursive: bool = True,
        glob: Optional[str] = None,
    ) -> GrepResult:
        try:
            if glob:
                cmd = (
                    f"find {shlex.quote(path)} -name {shlex.quote(glob)} -type f "
                    f"-exec grep -n {shlex.quote(pattern)} {{}} \\; 2>/dev/null || true"
                )
            else:
                flag = "-rn" if recursive else "-n"
                cmd = f"grep {flag} {shlex.quote(pattern)} {shlex.quote(path)} 2>/dev/null || true"
            stdout, _ = self.execute(cmd)
            matches = []
            for line in stdout.strip().split("\n"):
                if not line or ":" not in line:
                    continue
                parts = line.split(":", 2)
                if len(parts) == 3:
                    file_path, line_num, content = parts
                    try:
                        matches.append({"path": file_path, "line": int(line_num), "text": content})
                    except ValueError:
                        continue
            return GrepResult(matches=matches, error=None)
        except Exception as exc:
            return GrepResult(error=str(exc), matches=[])

    def close(self) -> None:
        if self.container:
            container_id = self.container.id[:12]
            self.container.stop()
            if not self.remove_on_close:
                self.container.remove()
            self.container = None
            print(f"Docker sandbox: cleaned {container_id}")


class AgentDockerSandbox(SandboxBackendProtocol):
    """Adapter that exposes DockerSandbox through SandboxBackendProtocol."""

    def __init__(self, target_project: Path) -> None:
        self._sandbox = DockerSandbox()
        self._sandbox.seed_directory(target_project, SANDBOX_PROJECT_ROOT)

    @property
    def id(self) -> str:
        return self._sandbox.container.id if self._sandbox.container else "closed"

    @property
    def image(self) -> str:
        return self._sandbox.image

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        output, exit_code = self._sandbox.execute(command, timeout=timeout)
        return ExecuteResponse(output=output, exit_code=exit_code)

    def ls(self, path: str = ".") -> LsResult:
        return self._sandbox.ls(path)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return self._sandbox.read(file_path, offset=offset, limit=limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        return self._sandbox.write(file_path, content)

    def edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        return self._sandbox.edit(file_path, old_string, new_string, replace_all=replace_all)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        return self._sandbox.glob(pattern, path or ".")

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None) -> GrepResult:
        return self._sandbox.grep(pattern, path or ".", glob=glob)

    def close(self) -> None:
        self._sandbox.close()


def _run_sandbox_json(sandbox: AgentDockerSandbox, code: str, cwd: str = SANDBOX_PROJECT_ROOT) -> dict:
    command = "python -c " + shlex.quote(code)
    output, exit_code = sandbox._sandbox.execute(f"cd {shlex.quote(cwd)} && {command}")
    if exit_code != 0:
        return {"ok": False, "exit_code": exit_code, "error": output.strip()}
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return {"ok": False, "exit_code": exit_code, "error": output.strip()}


def _relative_sandbox_path(path: str, root: str = SANDBOX_PROJECT_ROOT) -> str:
    normalized_path = path.rstrip("/")
    normalized_root = root.rstrip("/")
    if normalized_path == normalized_root:
        return "."
    if normalized_path.startswith(normalized_root + "/"):
        return normalized_path[len(normalized_root) + 1:]
    return normalized_path


def _optional_command(
    sandbox: AgentDockerSandbox,
    command: str,
    cwd: str = SANDBOX_PROJECT_ROOT,
    timeout_hint: str = "",
    max_output: int = 20000,
    timeout_seconds: int | None = None,
) -> dict:
    executable = shlex.split(command)[0] if command.strip() else ""
    check_cmd = "command -v " + shlex.quote(executable) + " >/dev/null 2>&1"
    _, found = sandbox._sandbox.execute(check_cmd)
    if found != 0:
        return {"available": False, "command": command, "cwd": cwd, "reason": "command not installed"}
    output, exit_code = sandbox._sandbox.execute(
        f"cd {shlex.quote(cwd)} && {command}",
        timeout=timeout_seconds,
    )
    result = {
        "available": True,
        "command": command,
        "cwd": cwd,
        "exit_code": exit_code,
        "output": output[:max_output],
        "output_truncated": len(output) > max_output,
        "note": timeout_hint,
    }
    if timeout_seconds is not None:
        result["timeout_seconds"] = timeout_seconds
        if exit_code == 124:
            result["timed_out"] = True
    stripped = output.strip()
    if stripped:
        try:
            result["json"] = json.loads(stripped)
        except json.JSONDecodeError:
            json_start = min(
                [idx for idx in (stripped.find("{"), stripped.find("[")) if idx >= 0],
                default=-1,
            )
            if json_start >= 0:
                try:
                    result["json"] = json.loads(stripped[json_start:])
                except json.JSONDecodeError:
                    pass
    return result


def _discover_project_files(sandbox: AgentDockerSandbox, target: str) -> dict:
    code = r'''
import json
from pathlib import Path

project_root = Path("PROJECT_ROOT_PLACEHOLDER")
target = Path("TARGET_PLACEHOLDER")
dependency_names = {
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "poetry.lock",
    "uv.lock",
    "Pipfile",
    "Pipfile.lock",
}
result = {
    "project_root": str(project_root),
    "target": str(target),
    "target_exists": target.exists(),
    "python_files": [],
    "dependency_files": [],
}
if target.exists():
    result["python_files"] = [str(path) for path in sorted(target.rglob("*.py"))]
if project_root.exists():
    result["dependency_files"] = [
        str(path)
        for path in sorted(project_root.rglob("*"))
        if path.is_file() and path.name in dependency_names
    ]
print(json.dumps(result, ensure_ascii=False))
'''.replace("PROJECT_ROOT_PLACEHOLDER", SANDBOX_PROJECT_ROOT).replace("TARGET_PLACEHOLDER", target)
    return _run_sandbox_json(sandbox, code)


def _skipped_command(command: str, reason: str) -> dict:
    return {
        "available": False,
        "command": command,
        "skipped": True,
        "reason": reason,
    }


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Middleware (M6: Observability)
# ---------------------------------------------------------------------------

@dataclass
class Metrics:
    model_calls: int = 0
    model_time_ms: float = 0.0
    tool_calls: int = 0
    tool_time_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    events: list = field(default_factory=list)
    by_thread: dict = field(default_factory=dict)

    def record_thread(self, thread_id: str, kind: str) -> None:
        bucket = self.by_thread.setdefault(thread_id, {"model": 0, "tool": 0})
        bucket[kind] = bucket.get(kind, 0) + 1

    def to_dict(self) -> dict:
        return {
            "model_calls": self.model_calls,
            "model_avg_ms": round(self.model_time_ms / max(1, self.model_calls), 1),
            "tool_calls": self.tool_calls,
            "tool_avg_ms": round(self.tool_time_ms / max(1, self.tool_calls), 1),
            "total_tokens": self.tokens_in + self.tokens_out,
            "by_thread": self.by_thread,
            "events": self.events,
        }


class PerfMiddleware(AgentMiddleware):
    """Custom performance middleware — tracks model calls, tool calls, tokens, timing."""

    def __init__(self, m: Metrics, verbose: bool = True):
        super().__init__()
        self.m = m
        self.verbose = verbose

    @staticmethod
    def _thread_id(request) -> str:
        try:
            config = get_config()
            configurable = config.get("configurable", {})
            if thread_id := configurable.get("thread_id"):
                return str(thread_id)
        except RuntimeError:
            pass

        runtime = getattr(request, "runtime", None)
        execution_info = getattr(runtime, "execution_info", None)
        if execution_info is not None and getattr(execution_info, "thread_id", None):
            return str(execution_info.thread_id)

        config = getattr(request, "config", None) or {}
        configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
        return str(configurable.get("thread_id", "unknown"))

    @staticmethod
    def _tool_name(request) -> str:
        tool = getattr(request, "tool", None)
        if tool is None:
            return "unknown"
        return getattr(tool, "name", str(tool))

    def wrap_model_call(self, request, handler):
        self.m.model_calls += 1
        thread_id = self._thread_id(request)
        self.m.record_thread(thread_id, "model")
        t0 = time.perf_counter()
        try:
            response = handler(request)
            dt = (time.perf_counter() - t0) * 1000
            self.m.model_time_ms += dt

            # Extract token usage from response
            actual = None
            if hasattr(response, 'result') and isinstance(response.result, list) and len(response.result) > 0:
                actual = response.result[0]
            elif hasattr(response, 'result'):
                actual = response.result
            if actual and hasattr(actual, 'usage_metadata'):
                usage = actual.usage_metadata or {}
                self.m.tokens_in += usage.get("input_tokens", 0)
                self.m.tokens_out += usage.get("output_tokens", 0)

            event = {"type": "model", "thread_id": thread_id, "ms": round(dt, 1)}
            self.m.events.append(event)
            if self.verbose:
                print(f"[debug] model_call thread={thread_id} ms={event['ms']}")
            return response
        except Exception:
            raise

    def wrap_tool_call(self, request, handler):
        self.m.tool_calls += 1
        thread_id = self._thread_id(request)
        self.m.record_thread(thread_id, "tool")
        t0 = time.perf_counter()
        tool_name = self._tool_name(request)
        try:
            result = handler(request)
            dt = (time.perf_counter() - t0) * 1000
            self.m.tool_time_ms += dt
            event = {"type": "tool", "thread_id": thread_id, "name": tool_name, "ms": round(dt, 1)}
            self.m.events.append(event)
            if self.verbose:
                print(f"[debug] tool_call thread={thread_id} tool={tool_name} ms={event['ms']}")
            return result
        except Exception as exc:
            event = {"type": "tool_error", "thread_id": thread_id, "name": tool_name, "error": type(exc).__name__}
            self.m.events.append(event)
            if self.verbose:
                print(f"[debug] tool_error thread={thread_id} tool={tool_name} error={type(exc).__name__}")
            raise


# ---------------------------------------------------------------------------
# Sub-agent definitions
# ---------------------------------------------------------------------------

def _code_health_subagents(ws: str, target: str) -> list:
    """Return the 5 sub-agents for code health orchestration."""
    language_rule = "输出语言硬性要求：全文使用中文，包括标题、表头、段落、结论和建议；工具名、文件路径、命令、错误码可以保留英文原文。"
    return [
        {
            "name": "architect",
            "description": "Analyze code architecture: directory structure, module coupling, layering",
            "system_prompt": f"""{language_rule}

你是代码架构师。分析 `{target}` 的架构：
- 先读取 {ws}/raw/tool_results.json，优先引用 discovery.python_files、tools.ruff、tools.mypy 的事实
- 如果 coverage_notes.mypy_mode=lightweight_ignore_missing_imports，必须说明 mypy 是轻量模式，结论不能等同完整 CI 类型检查
- 目录结构是否合理、模块粒度是否合适
- 是否存在循环依赖、God Class
- 分层是否清晰（API → 业务 → 数据）
将分析结果以 Markdown 写入 {ws}/drafts/architect.md，不要写 JSON/HTML，最后输出中文摘要。""",
            "skills": [_skill_dir("code-health/code-architect")],
        },
        {
            "name": "security-analyst",
            "description": "Audit code for vulnerabilities: SQL injection, XSS, hardcoded secrets, OWASP Top-10",
            "system_prompt": f"""{language_rule}

你是应用安全工程师。审计 `{target}` 的安全：
- 先读取 {ws}/raw/tool_results.json，优先引用 tools.bandit 和 tools.pip_audit 的事实
- SQL 注入、XSS、硬编码密钥
- 不安全反序列化、路径遍历
- 敏感端点认证缺失
将分析结果以 Markdown 写入 {ws}/drafts/security.md，不要写 JSON/HTML，最后输出中文摘要。""",
            "skills": [_skill_dir("code-health/code-security")],
        },
        {
            "name": "dependency-checker",
            "description": "Check dependencies: outdated versions, CVEs, license compatibility",
            "system_prompt": f"""{language_rule}

你是依赖管理专家。检查 `{target}` 的依赖：
- 先读取 {ws}/raw/tool_results.json，优先引用 discovery.dependency_files、setup.dependency_install、tools.pip_audit 的事实
- 如果 setup.dependency_install.skipped=true，必须说明项目依赖未安装，mypy 类型检查和依赖审计覆盖可能受限
- 可以按 web-search skill 使用 internet_search 查询公开依赖生态信息，并将需要引用的搜索证据保存到 {ws}/raw/web_search/；不能把搜索摘要当作已验证 CVE，具体漏洞仍以 pip-audit 或明确官方公告为准
- 主版本落后的包、已知 CVE
- 许可证兼容性、未声明的传递依赖
将分析结果以 Markdown 写入 {ws}/drafts/dependencies.md，不要写 JSON/HTML，最后输出中文摘要。""",
            "skills": [_shared_skill_dir(), _skill_dir("code-health/code-dependency")],
        },
        {
            "name": "doc-reviewer",
            "description": "Review documentation: README, docstrings, API docs coverage",
            "system_prompt": f"""{language_rule}

你是技术文档专家。评估 `{target}` 的文档：
- 先读取 {ws}/raw/tool_results.json，使用 discovery.python_files 明确实际扫描范围
- 文档/docstring 覆盖需要通过 read_file 阅读代码判断；不要声称已有自动 docstring 覆盖统计
- README 完整性、公开 API docstring 覆盖率
- 注释质量、配置说明
将分析结果以 Markdown 写入 {ws}/drafts/documentation.md，不要写 JSON/HTML，最后输出中文摘要。""",
            "skills": [_skill_dir("code-health/code-documentation")],
        },
        {
            "name": "summarizer",
            "description": "Merge 4 analysis reports into a consolidated code health report",
            "system_prompt": f"""{language_rule}

你是报告汇总专家。任务：
1. read_file 读取 {ws}/raw/tool_results.json
2. ls {ws}/drafts/ 发现所有分析报告
3. read_file 逐个读取
4. 去重合并 → 按严重度分级（Critical/High/Medium/Low）
5. 按 skill 中的评分建议计算健康度评分（0-100），并说明工具覆盖情况、mypy 轻量模式限制和置信度
6. 必须调用 write_file 工具，将最终报告以 Markdown 写入这个唯一最终路径：{ws}/code_health_report.md

报告使用中文 Markdown，包含执行摘要和趋势对比（如有历史数据）。
最终输出契约：
- 最终报告只能写入 {ws}/code_health_report.md
- 最终报告必须是 Markdown，不要生成 HTML、JSON、CSS、JS 或可视化网页
- 不要写入 /outputs/code_health_report.md、/outputs/core_code_health_report.md 或其他根级别文件名
- 不要写入 {ws}/drafts/merged-report.md 作为最终报告；drafts 目录只保存中间草稿
- 调用 write_file 成功写入 {ws}/code_health_report.md 后立即结束任务
- 不要再使用 execute 运行 ls、wc、cat、cp、touch 等命令验证、复制、重写或另存输出文件
- /outputs/ 路径只通过文件工具访问，不要在 execute 命令中读写 /outputs/。""",
            "skills": [_skill_dir("code-health/report-writing")],
        },
    ]


def _prd_review_subagents(ws: str) -> list:
    """Return the 5 sub-agents for PRD review orchestration (writer is the orchestrator itself)."""
    return [
        {
            "name": "product-strategist",
            "description": "Review PRD from strategy perspective: market, business value, differentiation",
            "system_prompt": f"""你是产品策略分析师。评审 PRD：
- 市场定位是否清晰、是否有明确竞品
- 商业价值和差异化在哪里
- MVP 功能优先级是否合理
- 可以按 web-search skill 使用 internet_search 检索公开竞品、市场和行业实践，并将需要引用的搜索证据保存到 {ws}/raw/web_search/；引用时必须保留链接，并说明这是公开资料补充，不是用户需求本身
读取 {ws}/prd_v1.md，将评审写入 {ws}/drafts/review_strategy.md，输出中文摘要。""",
            "skills": [_shared_skill_dir(), _skill_dir("prd-review/review-ops")],
        },
        {
            "name": "technical-feasibility",
            "description": "Review PRD from tech perspective: stack, architecture, integration risks",
            "system_prompt": f"""你是技术架构师。评审 PRD：
- 技术栈是否可行、是否需要大规模重构
- API 设计是否清晰、性能预期是否合理
- 是否有安全隐患
读取 {ws}/prd_v1.md，将评审写入 {ws}/drafts/review_tech.md，输出中文摘要。""",
            "skills": [_skill_dir("prd-review/review-tech")],
        },
        {
            "name": "ux-researcher",
            "description": "Review PRD from UX perspective: user journey, edge cases, accessibility",
            "system_prompt": f"""你是 UX 研究员。评审 PRD：
- 用户操作路径是否最短、异常状态是否覆盖
- 交互一致性、无障碍、信息架构
读取 {ws}/prd_v1.md，将评审写入 {ws}/drafts/review_ux.md，输出中文摘要。""",
            "skills": [_skill_dir("prd-review/review-ux")],
        },
        {
            "name": "risk-analyst",
            "description": "Review PRD from risk perspective: timeline, resources, privacy, adoption barriers",
            "system_prompt": f"""你是项目风险分析师。评审 PRD：
- 时间线和资源风险、团队能力匹配
- 数据隐私合规、用户采纳障碍
读取 {ws}/prd_v1.md，将评审写入 {ws}/drafts/review_risk.md，输出中文摘要。""",
            "skills": [_skill_dir("prd-review/review-test")],
        },
        {
            "name": "editor",
            "description": "Merge all reviews into a final polished PRD v2 with review matrix",
            "system_prompt": f"""你是 PRD 编辑。任务：
1. ls {ws}/drafts/ 发现所有评审报告
2. read_file 逐个读取 + 读取 {ws}/prd_v1.md
3. 综合反馈，生成修订版 PRD v2 → {ws}/prd_v2_final.md
4. 生成评审矩阵 → {ws}/review_matrix.md
   （格式：序号 | 维度 | 严重度 | 问题 | 建议 | 状态）
5. 必须调用 write_file 工具分别写入上述两个文件

报告使用中文。
最终输出契约：
- PRD v2 只能写入 {ws}/prd_v2_final.md
- 评审矩阵只能写入 {ws}/review_matrix.md
- 完成上述两个标准文件后立即停止，不要再创建副本、别名、merged 文件或根目录 /outputs/*.md 文件
- 不要写入 {ws}/final_report.md 作为最终报告；必须拆分为上述两个标准文件
- 调用 write_file 成功写入上述两个标准文件后立即结束任务
- 不要再使用 execute 运行 ls、wc、cat、cp、touch 等命令验证、复制、重写或另存输出文件
- /outputs/ 路径只通过文件工具访问，不要在 execute 命令中读写 /outputs/。""",
            "skills": [_skill_dir("prd-review/report-writing"), _skill_dir("prd-review/review-matrix")],
        },
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    # ---- LangSmith ----
    # langsmith_key = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY", "")
    # langsmith_enabled = bool(langsmith_key)
    # if langsmith_enabled:
    #     os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    #     os.environ.setdefault("LANGCHAIN_PROJECT", "aperio-integrated")
    #     print("LangSmith: ✅ enabled")
    # else:
    #     print("LangSmith: ⚠️  not configured")

    print("=" * 60)
    print("Aperio Integrated Demo")
    print("=" * 60)

    # ---- Validate ----
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not found")
        return
    target_project_path = (_PROJECT_ROOT / TARGET_PROJECT).resolve()
    target_path = (_PROJECT_ROOT / TARGET_CODE).resolve()
    if not target_project_path.exists():
        print(f"ERROR: target project '{TARGET_PROJECT}' not found")
        return
    if not target_path.exists():
        print(f"ERROR: target code '{TARGET_CODE}' not found")
        return

    run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    run_root = WORKSPACE_ROOT / run_id
    code_ws = "/outputs/code_health"
    prd_ws = "/outputs/prd_review"
    local_resources = setup_local_resources()
    (run_root / "code_health" / "drafts").mkdir(parents=True, exist_ok=True)
    (run_root / "prd_review" / "drafts").mkdir(parents=True, exist_ok=True)

    # ---- Backend (M4 + M5) ----
    store = InMemoryStore()
    sandbox: AgentDockerSandbox | None = None
    try:
        sandbox = AgentDockerSandbox(target_project_path)
    except Exception as exc:
        print(f"ERROR: Docker sandbox unavailable: {exc}")
        print("Run Docker Desktop, then rerun this demo in the llm-dev environment.")
        return
    atexit.register(sandbox.close)

    backend = CompositeBackend(
        default=sandbox,
        routes={
            "/outputs/": FilesystemBackend(root_dir=str(run_root), virtual_mode=True),
            "/memories/": StoreBackend(store=store, namespace=lambda rt: ("aperio",)),
            "/temp/": StateBackend(),
            "/local-resources/": FilesystemBackend(root_dir=str(local_resources), virtual_mode=True),
            "/skills/": FilesystemBackend(root_dir=str(SKILLS_DIR), virtual_mode=True),
        },
    )
    checkpointer = MemorySaver()
    permissions = [
        FilesystemPermission(
            operations=["write"],
            paths=["/outputs/*"],
            mode="deny",
        ),
        FilesystemPermission(
            operations=["write"],
            paths=["/local-resources/**"],
            mode="deny",
        ),
    ]

    @tool
    def run_code_health_checks(target: str = SANDBOX_TARGET_CODE) -> str:
        """Run code-health tools in the Docker sandbox and save JSON results."""
        target_rel = _relative_sandbox_path(target)
        discovery = _discover_project_files(sandbox, target)
        dependency_files = discovery.get("dependency_files", [])

        if INSTALL_PROJECT_DEPS:
            dependency_install = _optional_command(
                sandbox,
                "python -m pip install --disable-pip-version-check -e .",
                max_output=20000,
                timeout_seconds=180,
            )
        else:
            dependency_install = _skipped_command(
                "python -m pip install --disable-pip-version-check -e .",
                "disabled by default; set APERIO_INSTALL_PROJECT_DEPS=1 to install project dependencies inside the disposable container",
            )

        if dependency_files:
            pip_audit = _optional_command(
                sandbox,
                "pip-audit . --format json --progress-spinner off",
                max_output=30000,
                timeout_seconds=90,
            )
        else:
            pip_audit = _skipped_command(
                "pip-audit . --format json --progress-spinner off",
                "no dependency manifest found under the sandbox project root",
            )

        tool_results = {
            "schema_version": "code-health-tools-v2",
            "project_root": SANDBOX_PROJECT_ROOT,
            "target": target,
            "target_rel": target_rel,
            "coverage_notes": {
                "mypy_mode": "lightweight_ignore_missing_imports",
                "mypy_limitation": (
                    "Project dependencies are not installed by default, so mypy runs with "
                    "--ignore-missing-imports. Treat mypy findings as partial type-check "
                    "coverage, not a full CI-equivalent type check."
                ),
                "dependency_install_default": "disabled",
                "dependency_install_enable": "set APERIO_INSTALL_PROJECT_DEPS=1",
            },
            "discovery": discovery,
            "setup": {
                "dependency_install": dependency_install,
            },
            "tools": {
                "ruff": _optional_command(
                    sandbox,
                    f"ruff check {shlex.quote(target_rel)} --output-format json --no-cache",
                ),
                "mypy": _optional_command(
                    sandbox,
                    f"mypy {shlex.quote(target_rel)} --hide-error-context --no-error-summary --no-incremental --ignore-missing-imports",
                ),
                "bandit": _optional_command(
                    sandbox,
                    f"bandit -r {shlex.quote(target_rel)} -f json -q",
                    max_output=30000,
                ),
                "pip_audit": pip_audit,
            },
        }
        output_path = run_root / "code_health" / "raw" / "tool_results.json"
        _write_json(output_path, tool_results)
        summary = {
            "saved_to": "/outputs/code_health/raw/tool_results.json",
            "schema_version": tool_results["schema_version"],
            "project_root": SANDBOX_PROJECT_ROOT,
            "target": target,
            "mypy_mode": tool_results["coverage_notes"]["mypy_mode"],
            "python_files": len(discovery.get("python_files", [])),
            "dependency_files": dependency_files,
            "dependency_install_exit_code": dependency_install.get("exit_code"),
            "tools_available": {
                name: data.get("available", False)
                for name, data in tool_results["tools"].items()
            },
            "tool_exit_codes": {
                name: data.get("exit_code")
                for name, data in tool_results["tools"].items()
            },
        }
        return json.dumps(summary, ensure_ascii=False)

    @tool
    def internet_search(query: str, max_results: int = 3, save_path: str = "") -> str:
        """Search the public web with DuckDuckGo, optionally saving JSON evidence under /outputs/."""
        query = (query or "").strip()
        save_path = (save_path or "").strip().replace("\\", "/")
        if not query:
            return json.dumps(
                {"ok": False, "error": "query is empty", "results": []},
                ensure_ascii=False,
            )
        if save_path and (
            not save_path.startswith("/outputs/")
            or not save_path.endswith(".json")
            or "/../" in save_path
            or save_path.startswith("/outputs/../")
        ):
            return json.dumps(
                {
                    "ok": False,
                    "query": query,
                    "error": "save_path must be a JSON file under /outputs/",
                    "results": [],
                },
                ensure_ascii=False,
            )

        try:
            limit = max(1, min(int(max_results), 5))
        except (TypeError, ValueError):
            limit = 3

        try:
            from ddgs import DDGS
        except Exception as exc:
            return json.dumps(
                {
                    "ok": False,
                    "query": query,
                    "error": f"ddgs is not available: {type(exc).__name__}: {exc}",
                    "results": [],
                },
                ensure_ascii=False,
            )

        try:
            with DDGS() as ddgs:
                raw_results = list(
                    ddgs.text(
                        query,
                        max_results=limit,
                        safesearch="moderate",
                    )
                )
        except Exception as exc:
            return json.dumps(
                {
                    "ok": False,
                    "query": query,
                    "error": f"search failed: {type(exc).__name__}: {exc}",
                    "results": [],
                },
                ensure_ascii=False,
            )

        results = []
        for item in raw_results[:limit]:
            title = str(item.get("title") or "").strip()
            snippet = str(item.get("body") or item.get("snippet") or "").strip()
            url = str(item.get("href") or item.get("url") or "").strip()
            if not title and not snippet and not url:
                continue
            results.append(
                {
                    "title": title,
                    "snippet": snippet[:600],
                    "url": url,
                }
            )

        payload = {
            "ok": True,
            "query": query,
            "max_results": limit,
            "results": results,
            "evidence_policy": (
                "Use these as public web evidence only. Do not replace local "
                "repository facts, tool results, or project files with web snippets."
            ),
        }
        if save_path:
            local_path = run_root / save_path.removeprefix("/outputs/")
            _write_json(local_path, payload)
            payload["saved_to"] = save_path

        return json.dumps(payload, ensure_ascii=False)

    # ---- Model ----
    primary_model_name = os.environ.get("APERIO_PRIMARY_MODEL", "openai:deepseek-v4-flash")
    fallback_model_name = os.environ.get("APERIO_FALLBACK_MODEL", "openai:deepseek-v4-flash")
    model_rate_limiter = InMemoryRateLimiter(
        requests_per_second=20 / 60,
        check_every_n_seconds=0.1,
        max_bucket_size=10,
    )
    model = init_chat_model(
        model=primary_model_name,
        api_key=api_key,
        base_url="https://api.deepseek.com",
        rate_limiter=model_rate_limiter,
    )
    fallback_model = init_chat_model(
        model=fallback_model_name,
        api_key=api_key,
        base_url="https://api.deepseek.com",
        rate_limiter=model_rate_limiter,
    )

    # ---- Middleware ----
    metrics = Metrics()
    perf = PerfMiddleware(metrics)
    user_middleware: list[AgentMiddleware] = [
        ModelCallLimitMiddleware(
            run_limit=100,
            exit_behavior="end",
        ),
        ModelRetryMiddleware(
            max_retries=3,
            initial_delay=1.0,
            max_delay=8.0,
            on_failure="error",
        ),
        ModelFallbackMiddleware(fallback_model),
        perf,
        ToolRetryMiddleware(
            tools=["ls", "glob", "grep", "read_file", "run_code_health_checks", "internet_search"],
            max_retries=2,
            initial_delay=0.5,
            max_delay=2.0,
            on_failure="continue",
        ),
        ToolCallLimitMiddleware(
            run_limit=160,
            exit_behavior="continue",
        ),
    ]

    code_health_orchestrator = {
        "name": "code-health-orchestrator",
        "description": "Orchestrate a code health scan: spawn 4 parallel analysis sub-agents then merge results",
        "system_prompt": f"""你是代码健康检查编排器（Code Health Orchestrator）。工作流程：

1. 使用 write_todos 规划任务
2. 调用 run_code_health_checks 工具扫描 {SANDBOX_TARGET_CODE}，工具结果会写入 {code_ws}/raw/tool_results.json
3. 可先读取 /local-resources/aperio_policy.yaml 了解安全和存储策略
4. 并行派发 4 个子代理分析 {SANDBOX_TARGET_CODE}，并要求它们优先引用 {code_ws}/raw/tool_results.json 中的事实：
   - architect：架构分析
   - security-analyst：安全审计
   - dependency-checker：依赖检查
   - doc-reviewer：文档评估
   每个子代理将结果写入 {code_ws}/drafts/
5. 等待全部 4 个完成后，派发 summarizer 合并生成最终报告
6. summarizer 的唯一最终产物必须是 Markdown 文件 {code_ws}/code_health_report.md；不要接受 HTML、JSON、/outputs/*.md 或 {code_ws}/drafts/merged-report.md 作为最终报告
7. summarizer 写入最终报告后流程结束；不要再使用 execute 验证、复制、重写或另存 /outputs/ 中的文件
8. 如有 /memories/history/ 中的历史数据，进行趋势对比

硬性约束：
- code-health 的 4 个草稿和最终报告必须全文使用中文，包括标题、表头、段落、结论和建议；工具名、文件路径、命令、错误码可以保留英文原文
- internet_search 是只读联网工具，只能作为公开资料补充；代码事实、扫描结论和最终风险判定必须优先来自本地代码、{code_ws}/raw/tool_results.json 和草稿报告
- 如果引用 internet_search 结果，必须明确标注其为公开网络证据，并保留链接；联网失败时不要编造公开资料
- 必须先得到这 4 个草稿文件：{code_ws}/drafts/architect.md、{code_ws}/drafts/security.md、{code_ws}/drafts/dependencies.md、{code_ws}/drafts/documentation.md
- 缺少任意一个草稿文件时，不要派发 summarizer，不要直接写最终报告
- 不要写入 {code_ws}/drafts/code_health_report.md；drafts 目录只放四个角色草稿
- 不要写入 /outputs/code_health_report.md；最终报告只能是 {code_ws}/code_health_report.md""",
        "subagents": _code_health_subagents(code_ws, SANDBOX_TARGET_CODE),
    }
    prd_review_orchestrator = {
        "name": "prd-review-orchestrator",
        "description": "Orchestrate PRD review: write a PRD, spawn 4 parallel reviewers, then editor merges feedback",
        "system_prompt": f"""你是 PRD 评审编排器（PRD Review Orchestrator）。工作流程：

1. 可先读取 /local-resources/aperio_policy.yaml 了解安全和存储策略
2. 根据用户需求，自己作为 Writer 编写 PRD 初稿 → {prd_ws}/prd_v1.md
   包含：产品概述、用户画像、核心功能（P0/P1/P2）、用户故事、成功指标、非功能需求
3. 并行派发 4 个评审子代理：
   - product-strategist：产品策略
   - technical-feasibility：技术可行性
   - ux-researcher：用户体验
   - risk-analyst：风险评估
   每个子代理读取 {prd_ws}/prd_v1.md，将评审写入 {prd_ws}/drafts/
4. 等待全部 4 个完成后，派发 editor 合并生成 PRD v2 + 评审矩阵
5. editor 的最终产物必须拆分为 {prd_ws}/prd_v2_final.md 和 {prd_ws}/review_matrix.md
6. 上述两个文件写入后流程即完成，不要再创建根目录 /outputs/*.md、别名文件、merged 文件或 {prd_ws}/final_report.md
7. 不要再使用 execute 验证、复制、重写或另存 /outputs/ 中的文件

PRD 使用中文撰写。""",
        "subagents": _prd_review_subagents(prd_ws),
    }
    subagent_specs = [code_health_orchestrator, prd_review_orchestrator]
    custom_tools = [run_code_health_checks, internet_search]

    print_backend_debug(run_root, local_resources, sandbox)
    print_sandbox_tool_debug(sandbox)
    print_subagent_debug(subagent_specs, backend)
    print_tool_debug(subagent_specs, custom_tools)
    print_middleware_debug(user_middleware)

    # ---- Main Router Agent ----
    agent = create_deep_agent(
        model=model,
        tools=custom_tools,
        backend=backend,
        checkpointer=checkpointer,
        middleware=user_middleware,
        permissions=permissions,
        interrupt_on={
            "execute": {"allowed_decisions": ["approve", "reject"]},
            "write_file": {"allowed_decisions": ["approve", "reject"]},
        },
        system_prompt="""你是 Aperio 研发质量平台的**任务路由器**。根据用户输入判断任务类型：

- 如果用户要求**分析代码、代码体检、代码健康检查**→ 委托给 code-health-orchestrator
- 如果用户要求**写 PRD、评审需求、产品需求文档**→ 委托给 prd-review-orchestrator
- internet_search 是只读联网工具，可用于公开资料检索；不要用它替代本地文件、工具结果或用户输入事实

CompositeBackend 分层存储策略：
1. 默认路径 → DockerSandbox，所有未匹配路径和 execute 工具都在隔离容器中执行
2. /outputs/ → 本次运行的本地输出区，用于保存 code_health 和 prd_review 报告
3. /temp/ → StateBackend，适合会话内临时文件
4. /memories/ → StoreBackend，适合跨线程共享记忆和历史趋势
5. /local-resources/ → 只读 FilesystemBackend，可读取 aperio_policy.yaml，但禁止写入

你的职责只是识别任务类型并路由到正确的 Orchestrator，不要自己做分析。""",
        subagents=subagent_specs,
    )

    # ---- Run: Code Health ----
    print(f"\n{'─' * 60}")
    print("Task 1: Code Health Scan")
    print(f"Target: {TARGET_CODE}")
    print(f"{'─' * 60}")

    t0 = time.time()
    code_config = {"configurable": {"thread_id": f"{run_id}:code-health"}}
    resp = agent.invoke(
        {
            "messages": [{
                "role": "user",
                "content": (
                    f"请对我的代码库 `{TARGET_CODE}` 进行完整的代码健康检查。"
                    f"使用 code-health-orchestrator 执行全流程，并写入报告。"
                ),
            }],
        },
        config=code_config,
        version="v2",
    )
    resp = handle_human_approval(agent, resp, code_config, "code-health")
    t_code = time.time() - t0

    # ---- Run: PRD Review ----
    print(f"\n{'─' * 60}")
    print("Task 2: PRD Review")
    print(f"{'─' * 60}")

    t1 = time.time()
    prd_config = {"configurable": {"thread_id": f"{run_id}:prd-review"}}
    resp = agent.invoke(
        {
            "messages": [{
                "role": "user",
                "content": (
                    "我需要为「智慧校园导航助手」写一份 PRD 并评审。"
                    "这是一款基于 AR/语音的校内导航 App，帮助新生和访客快速找到教室和设施，"
                    "同时提供校园活动推荐和实时拥挤度信息。"
                    "请使用 prd-review-orchestrator 执行全流程，并写入标准 PRD v2 和评审矩阵。"
                ),
            }],
        },
        config=prd_config,
        version="v2",
    )
    resp = handle_human_approval(agent, resp, prd_config, "prd-review")
    t_prd = time.time() - t1

    # ---- Output ----
    print(f"\n{'=' * 60}")

    # Performance
    m = metrics.to_dict()
    print(f"📊 Performance:")
    print(f"   Code Health:   {t_code:.1f}s")
    print(f"   PRD Review:    {t_prd:.1f}s")
    print(f"   Total wall:    {t_code + t_prd:.1f}s")
    print(f"   Model calls:   {m['model_calls']} (avg {m['model_avg_ms']}ms)")
    print(f"   Tool calls:    {m['tool_calls']} (avg {m['tool_avg_ms']}ms)")
    print(f"   Total tokens:  {m['total_tokens']}")

    # Save perf
    (run_root / "performance.json").write_text(
        json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")

    # Output files
    print(f"\n📁 Workspace: {run_root}/")
    for f in sorted(run_root.rglob("*")):
        if f.is_file():
            print(f"   {f.relative_to(run_root)} ({f.stat().st_size} bytes)")
    print_expected_outputs(run_root)

    sandbox.close()
    print(f"\n✅ Aperio Integrated Demo complete!")


if __name__ == "__main__":
    main()
