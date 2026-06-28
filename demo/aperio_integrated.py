"""
Aperio Integrated Demo — task router + two orchestrators with skill-equipped sub-agents.

Architecture:
  Main Router
    ├── code-health-orchestrator  (sync)
    │     ├── architect           (async, skill: code-architect)
    │     ├── security-analyst    (async, skill: code-security)
    │     ├── dependency-checker  (async, skill: code-dependency)
    │     ├── doc-reviewer        (async, skill: code-documentation)
    │     └── summarizer          (sync,  skill: report-writing-code-health)
    │
    └── prd-review-orchestrator   (sync)
          ├── product-strategist  (async, skill: review-ops)
          ├── technical-feasibility(async,skill: review-tech)
          ├── ux-researcher       (async, skill: review-ux)
          ├── risk-analyst        (async, skill: review-risk)
          └── editor              (sync,  skill: report-writing-prd + review-matrix)

Usage:
  conda activate llm-dev
  python demo/aperio_integrated.py
"""
from __future__ import annotations

import base64
import atexit
import asyncio
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

from deepagents import FilesystemPermission, HarnessProfile, create_deep_agent, register_harness_profile
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
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelFallbackMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langgraph.config import get_config
from langgraph.errors import GraphInterrupt
from langgraph.types import Command
from langchain_mcp_adapters.client import MultiServerMCPClient
from deepagents.middleware.skills import _list_skills_with_errors
from deepagents.middleware.summarization import SummarizationMiddleware, compute_summarization_defaults

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WORKSPACE_ROOT = (_PROJECT_ROOT / "demo/workspace_integrated").resolve()
SKILLS_DIR = _DEMO_DIR / "04_skills"
LOCAL_RESOURCES_DIR = _DEMO_DIR / "local_resources"
SANDBOX_PROJECT_ROOT = "/workspace/project"
DEFAULT_SANDBOX_IMAGE = "aperio-sandbox:py311-tools"
SANDBOX_IMAGE = os.environ.get("APERIO_SANDBOX_IMAGE", DEFAULT_SANDBOX_IMAGE)
SANDBOX_DOCKERFILE = (_DEMO_DIR / "sandbox" / "Dockerfile").resolve()
INSTALL_PROJECT_DEPS = os.environ.get("APERIO_INSTALL_PROJECT_DEPS", "0") == "1"


def _resolve_host_path(path: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = _PROJECT_ROOT / candidate
    return candidate.resolve()


def _normalize_relative_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip().strip("/")
    return "." if normalized in {"", "."} else normalized


def _relative_path(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve())
        return _normalize_relative_path(str(rel).replace("\\", "/"))
    except ValueError:
        return "."


PROJECT_ROOT_MARKERS = (
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "poetry.lock",
    "uv.lock",
    "package.json",
    ".git",
)


def read_runtime_task() -> str:
    """Read the user task at runtime; empty input means the current workspace."""
    print("\n请输入任务，例如：对 path/to/project/src 做完整的代码健康检查")
    try:
        task = input("> ").strip()
    except EOFError:
        task = ""
    return task or "请对当前工作区做完整的代码健康检查。"


def _clean_path_candidate(text: str) -> str:
    candidate = text.strip().strip("`'\"“”‘’「」『』（）()[]【】")
    prefixes = (
        "我的代码库",
        "这个代码库",
        "代码库",
        "项目",
        "目录",
        "路径",
        "当前",
    )
    for prefix in prefixes:
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix):].strip(" ：:，,")
    candidate = re.split(
        r"(?:做|进行|执行|开展|生成|输出|写入|完整|全面|代码健康|代码体检|代码检查|检查|体检|分析)",
        candidate,
        maxsplit=1,
    )[0].strip(" ：:，,。.;；")
    return candidate


def extract_path_candidates(task_text: str) -> list[str]:
    """Extract likely filesystem paths from a runtime natural-language task."""
    candidates: list[str] = []
    for match in re.finditer(r"[`'\"“「『](.+?)[`'\"”」』]", task_text):
        candidates.append(_clean_path_candidate(match.group(1)))

    for pattern in (
        r"(?:对|给|扫描|检查|分析|体检)\s*(.+?)(?:做|进行|执行|开展|生成|输出|写入|$)",
        r"(?:代码库|项目|目录|路径)\s*[:：]?\s*(.+?)(?:\s|，|。|；|;|$)",
    ):
        for match in re.finditer(pattern, task_text):
            candidates.append(_clean_path_candidate(match.group(1)))

    for token in re.split(r"\s+", task_text):
        cleaned = _clean_path_candidate(token)
        if "/" in cleaned or "\\" in cleaned or re.match(r"^[A-Za-z]:", cleaned):
            candidates.append(cleaned)

    ordered: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in ordered:
            ordered.append(candidate)
    return ordered


def infer_project_root(target_path: Path) -> Path:
    """Find the closest project root for a target path using common manifests."""
    current = target_path if target_path.is_dir() else target_path.parent
    while True:
        if any((current / marker).exists() for marker in PROJECT_ROOT_MARKERS):
            return current
        if current.parent == current:
            return target_path if target_path.is_dir() else target_path.parent
        current = current.parent


def resolve_code_health_input(task_text: str) -> tuple[Path, str, Path]:
    """Resolve runtime task text into host project root, target rel path, and target path."""
    for candidate in extract_path_candidates(task_text):
        candidate_path = _resolve_host_path(candidate)
        if not candidate_path.exists():
            continue
        project_root = infer_project_root(candidate_path)
        return project_root, _relative_path(candidate_path, project_root), candidate_path

    project_root = _PROJECT_ROOT.resolve()
    return project_root, ".", project_root

def _skill_dir(name: str) -> str:
    """Build a virtual skill source routed through CompositeBackend."""
    return "/skills/" + name.replace("\\", "/").strip("/")


def _shared_skill_dir() -> str:
    """Return the shared-skill source under /skills/shared."""
    return _skill_dir("shared")


def _agent_skills(*sources: str) -> list[str]:
    """Attach shared skills plus agent-specific skill sources."""
    ordered = [_shared_skill_dir(), *sources]
    return list(dict.fromkeys(ordered))


def setup_local_resources(code_target_rel: str) -> Path:
    """Create read-only local resources exposed through CompositeBackend."""
    LOCAL_RESOURCES_DIR.mkdir(parents=True, exist_ok=True)
    policy_file = LOCAL_RESOURCES_DIR / "aperio_policy.yaml"
    sandbox_source_path = (
        SANDBOX_PROJECT_ROOT if code_target_rel == "." else f"{SANDBOX_PROJECT_ROOT}/{code_target_rel}"
    )
    policy_file.write_text(
        f"""security:
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
  source_path: {sandbox_source_path}
  target_relative_path: {code_target_rel}
  draft_dir: /outputs/code_health/drafts
  final_report: /outputs/code_health/code_health_report.md
  toolchain:
    image: aperio-sandbox:py311-tools
    preinstalled:
      - ruff
      - mypy
      - bandit
      - pip-audit
      - pytest
      - coverage
      - deptry
      - interrogate
      - radon
      - detect-secrets
    schema: code-health-tools-v5
    install_project_deps_default: false
    mypy_default_mode: lightweight_ignore_missing_imports
    mypy_limitation: "project dependencies are not installed by default; mypy results are partial and not CI-equivalent"
    pytest_coverage_limitation: "pytest and coverage depend on discovered tests and installed project dependencies"
    deptry_limitation: "without installed project dependencies, transitive dependency analysis can be incomplete"
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
        "pytest": "python -m pytest --version",
        "coverage": "python -m coverage --version",
        "deptry": "deptry --version",
        "interrogate": "interrogate --version",
        "radon": "radon --version",
        "detect-secrets": "detect-secrets --version",
    }
    for name, command in commands.items():
        output, exit_code = sandbox._sandbox.execute(command)
        first_line = output.strip().splitlines()[0] if output.strip() else ""
        status = "OK" if exit_code == 0 else "MISSING"
        print(f"  {status:7} {name}: {first_line or 'not available'}")


def print_subagent_debug(subagents: list[dict], backend: CompositeBackend) -> None:
    print("\n[debug] Subagents and skills")
    print("  note: declared subagents build their own middleware stack; this demo injects PerfMiddleware into each declared agent.")
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
        if "runnable" in spec:
            print(f"  - {name}: tools=preconfigured in compiled runnable [no parent inheritance]")
        elif "tools" in spec:
            tool_names = [getattr(tool_item, "name", getattr(tool_item, "__name__", str(tool_item))) for tool_item in spec["tools"]]
            print(f"  - {name}: tools={tool_names} [override]")
        else:
            print(f"  - {name}: tools=inherited + custom={custom_tool_names or '[]'}")


def print_subagent_middleware_debug(subagents: list[dict]) -> None:
    print("\n[debug] Subagent middleware")
    for name, spec in _collect_subagents(subagents):
        middleware = spec.get("middleware", [])
        middleware_names = [getattr(item, "name", type(item).__name__) for item in middleware]
        print(f"  - {name}: middleware={middleware_names or '[]'}")


def print_middleware_debug(middleware: list[AgentMiddleware]) -> None:
    print("\n[debug] User-configured middleware")
    print("  note: DeepAgents still installs TodoList, Filesystem, SubAgent, and HITL internally.")
    print("  note: default SummarizationMiddleware is excluded by HarnessProfile; ObservableSummarizationMiddleware is injected explicitly.")
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


def _write_file_path(args: dict) -> str:
    return str(
        args.get("file_path")
        or args.get("path")
        or args.get("filepath")
        or args.get("file")
        or args.get("filename")
        or args.get("name")
        or ""
    ).replace("\\", "/")


def _interrupt_count(exc: GraphInterrupt) -> int:
    if not exc.args:
        return 0
    interrupts = exc.args[0]
    try:
        return len(interrupts)
    except TypeError:
        return 1


def _normalize_virtual_path(path: str) -> str:
    return "/" + path.replace("\\", "/").strip("/")


def _normalize_shell_command(command: str) -> str:
    return re.sub(r"\s+", " ", command.strip())


def _redact_preview(value: str, limit: int = 240) -> str:
    redacted = re.sub(
        r"(?i)\b(api[_-]?key|token|password|passwd|secret|authorization)\s*=\s*([^\s]+)",
        r"\1=<redacted>",
        value,
    )
    redacted = redacted.replace("\n", "\\n")
    return redacted[:limit] + ("..." if len(redacted) > limit else "")


def _tool_event_preview(tool_name: str, args: dict) -> dict:
    if tool_name == "execute":
        return {"command_preview": _redact_preview(str(args.get("command") or ""))}
    if tool_name in {"read_file", "write_file", "edit_file", "ls", "glob", "grep"}:
        preview: dict[str, object] = {}
        path = _write_file_path(args) or args.get("file_path") or args.get("path") or args.get("pattern")
        if path:
            preview["path"] = _redact_preview(str(path), limit=180)
        if tool_name == "write_file":
            preview["content_chars"] = len(str(args.get("content") or ""))
        return preview
    return {}


_FINAL_WRITE_PATHS = {
    "/outputs/code_health/code_health_report.md",
    "/outputs/prd_review/prd_v2_final.md",
    "/outputs/prd_review/review_matrix.md",
}
_AUTO_WRITE_PATH_RE = re.compile(
    r"^/outputs/(?:"
    r"code_health/drafts/(?:architect|security|dependencies|documentation)\.md"
    r"|prd_review/prd_v1\.md"
    r"|prd_review/drafts/review_(?:strategy|tech|ux|risk)\.md"
    r")$"
)


def _write_requires_approval(request) -> bool:
    """Require HITL for final artifacts and unexpected writes, not standard drafts."""
    args = request.tool_call.get("args", {})
    path = _normalize_virtual_path(_write_file_path(args))
    if path in _FINAL_WRITE_PATHS:
        return True
    if _AUTO_WRITE_PATH_RE.fullmatch(path):
        return False
    return True


_SAFE_MKDIR_RE = re.compile(
    r"^\s*mkdir\s+-p\s+(?:['\"]?(?:/outputs|/temp|/tmp)(?:/[^\s;&|<>]*)?['\"]?\s*)+$"
)
_HIGH_RISK_EXECUTE_RE = re.compile(
    r"""
    (^|[\s;&|])(rm|rmdir|unlink|shred|mkdir|mv|cp|chmod|chown|chgrp|truncate|dd|mkfs|mount|umount|sudo|docker|podman)\b
    |\b(pip|pip3|uv|poetry|pipenv|conda|npm|pnpm|yarn|bun|apt|apt-get|apk|yum|dnf|brew|cargo|go)\s+(install|add|remove|uninstall|update|upgrade|sync|get|mod)\b
    |\bgit\s+(reset|clean|checkout|restore|switch|pull|merge|rebase|push|commit|tag)\b
    |(^|[\s;&|])tee\s+
    |(?:>>?|<)
    """,
    re.IGNORECASE | re.VERBOSE,
)
_INLINE_CODE_EXECUTE_RE = re.compile(
    r"\b(python|python3|node|ruby|perl|bash|sh)\s+(-c|-m\s+pip|<<|-)\b",
    re.IGNORECASE,
)
_SCRIPT_OR_MODULE_EXECUTE_RE = re.compile(
    r"(^|[\s;&|])(?:python(?:3)?|node|ruby|perl|bash|sh)\s+"
    r"(?:-m\s+[A-Za-z0-9_.-]+|[^\s;&|<>]+\.(?:py|js|mjs|cjs|rb|pl|sh|bash))\b",
    re.IGNORECASE,
)


def _execute_requires_approval(request) -> bool:
    """Return True only for high-risk shell commands.

    Low-risk read commands and output-directory setup should not trigger HITL;
    destructive operations, dependency installs, shell redirection, inline code,
    and script/module execution still require human approval.
    """
    args = request.tool_call.get("args", {})
    command = str(args.get("command") or "").strip()
    if not command:
        return True
    if _SAFE_MKDIR_RE.fullmatch(command):
        return False
    if _HIGH_RISK_EXECUTE_RE.search(command):
        return True
    if _INLINE_CODE_EXECUTE_RE.search(command):
        return True
    if _SCRIPT_OR_MODULE_EXECUTE_RE.search(command):
        return True
    return False


def handle_human_approval(agent, response, config: dict, label: str):
    print(f"[debug] {label} response_type={type(response).__name__} interrupts={len(getattr(response, 'interrupts', []) or [])}")
    while hasattr(response, "interrupts") and response.interrupts:
        resume_map = {}
        single_resume_payload = None
        for interrupt in response.interrupts:
            decisions = []
            for action in interrupt.value.get("action_requests", []):
                print(f"\n  ⏸️  HITL: Agent 请求执行 [{action['name']}]")
                if action['name'] == 'execute':
                    command = action['args'].get('command', 'N/A')
                    print(f"     命令: {command}")
                elif action['name'] == 'write_file':
                    file_path = _write_file_path(action['args']) or 'N/A'
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
            payload = {"decisions": decisions}
            single_resume_payload = payload
            resume_map[interrupt.id] = payload
        resume_payload = single_resume_payload if len(response.interrupts) == 1 else resume_map
        response = agent.invoke(Command(resume=resume_payload), config=config, version="v2")
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
        volumes: dict[str, dict[str, str]] | None = None,
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
            volumes=volumes,
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

    def __init__(self, target_project: Path, skills_dir: Path, outputs_dir: Path) -> None:
        outputs_dir.mkdir(parents=True, exist_ok=True)
        volumes = {
            str(skills_dir.resolve()): {"bind": "/skills", "mode": "ro"},
            str(outputs_dir.resolve()): {"bind": "/outputs", "mode": "rw"},
        }
        self._sandbox = DockerSandbox(volumes=volumes)
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


def _load_mcp_tools(run_root: Path) -> list:
    """Load host-side MCP tools for the current Aperio run."""
    server_path = _DEMO_DIR / "mcp_web_search_server.py"
    client = MultiServerMCPClient(
        {
            "web_search": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(server_path)],
                "cwd": str(_PROJECT_ROOT),
                "env": {
                    **os.environ,
                    "APERIO_OUTPUTS_DIR": str(run_root),
                    "FASTMCP_LOG_LEVEL": "ERROR",
                    "PYTHONIOENCODING": "utf-8",
                },
            }
        },
        handle_tool_errors=True,
    )
    return asyncio.run(client.get_tools())


# ---------------------------------------------------------------------------
# Middleware (M6: Observability)
# ---------------------------------------------------------------------------

@dataclass
class Metrics:
    model_calls: int = 0
    model_time_ms: float = 0.0
    tool_calls: int = 0
    tool_time_ms: float = 0.0
    summarization_events: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    events: list = field(default_factory=list)
    by_thread: dict = field(default_factory=dict)
    by_agent: dict = field(default_factory=dict)
    by_agent_thread: dict = field(default_factory=dict)

    def record_thread(self, thread_id: str, kind: str) -> None:
        bucket = self.by_thread.setdefault(thread_id, {"model": 0, "tool": 0})
        bucket[kind] = bucket.get(kind, 0) + 1

    def record_agent(self, agent_name: str, thread_id: str, kind: str) -> None:
        agent_bucket = self.by_agent.setdefault(agent_name, {"model": 0, "tool": 0})
        agent_bucket[kind] = agent_bucket.get(kind, 0) + 1
        key = f"{thread_id}::{agent_name}"
        scoped_bucket = self.by_agent_thread.setdefault(key, {"model": 0, "tool": 0})
        scoped_bucket[kind] = scoped_bucket.get(kind, 0) + 1

    def to_dict(self) -> dict:
        return {
            "model_calls": self.model_calls,
            "model_avg_ms": round(self.model_time_ms / max(1, self.model_calls), 1),
            "tool_calls": self.tool_calls,
            "tool_avg_ms": round(self.tool_time_ms / max(1, self.tool_calls), 1),
            "summarization_events": self.summarization_events,
            "total_tokens": self.tokens_in + self.tokens_out,
            "by_thread": self.by_thread,
            "by_agent": self.by_agent,
            "by_agent_thread": self.by_agent_thread,
            "events": self.events,
        }


class PerfMiddleware(AgentMiddleware):
    """Custom performance middleware — tracks model calls, tool calls, tokens, timing."""

    def __init__(self, m: Metrics, agent_name: str, verbose: bool = True):
        super().__init__()
        self.m = m
        self.agent_name = agent_name
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
        self.m.record_agent(self.agent_name, thread_id, "model")
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

            event = {"type": "model", "agent": self.agent_name, "thread_id": thread_id, "ms": round(dt, 1)}
            self.m.events.append(event)
            if self.verbose:
                print(f"[debug] model_call agent={self.agent_name} thread={thread_id} ms={event['ms']}")
            return response
        except Exception:
            raise

    def wrap_tool_call(self, request, handler):
        self.m.tool_calls += 1
        thread_id = self._thread_id(request)
        self.m.record_thread(thread_id, "tool")
        self.m.record_agent(self.agent_name, thread_id, "tool")
        t0 = time.perf_counter()
        tool_name = self._tool_name(request)
        try:
            result = handler(request)
            dt = (time.perf_counter() - t0) * 1000
            self.m.tool_time_ms += dt
            event = {
                "type": "tool",
                "agent": self.agent_name,
                "thread_id": thread_id,
                "name": tool_name,
                "ms": round(dt, 1),
            }
            event.update(_tool_event_preview(tool_name, request.tool_call.get("args", {})))
            self.m.events.append(event)
            if self.verbose:
                print(f"[debug] tool_call agent={self.agent_name} thread={thread_id} tool={tool_name} ms={event['ms']}")
            return result
        except GraphInterrupt as exc:
            event = {
                "type": "tool_interrupt",
                "agent": self.agent_name,
                "thread_id": thread_id,
                "name": tool_name,
                "interrupts": _interrupt_count(exc),
            }
            event.update(_tool_event_preview(tool_name, request.tool_call.get("args", {})))
            self.m.events.append(event)
            if self.verbose:
                print(f"[debug] tool_interrupt agent={self.agent_name} thread={thread_id} tool={tool_name}")
            raise
        except Exception as exc:
            event = {
                "type": "tool_error",
                "agent": self.agent_name,
                "thread_id": thread_id,
                "name": tool_name,
                "error": type(exc).__name__,
            }
            event.update(_tool_event_preview(tool_name, request.tool_call.get("args", {})))
            self.m.events.append(event)
            if self.verbose:
                print(f"[debug] tool_error agent={self.agent_name} thread={thread_id} tool={tool_name} error={type(exc).__name__}")
            raise


class ToolAllowlistMiddleware(AgentMiddleware):
    """Hide and block tools outside a role-specific allowlist."""

    def __init__(self, allowed_tools: set[str], label: str):
        super().__init__()
        self.allowed_tools = allowed_tools
        self.label = label

    @staticmethod
    def _tool_name(tool) -> str:
        if isinstance(tool, dict):
            function = tool.get("function")
            if isinstance(function, dict) and function.get("name"):
                return str(function["name"])
            if tool.get("name"):
                return str(tool["name"])
            return ""
        return str(getattr(tool, "name", ""))

    def wrap_model_call(self, request, handler):
        filtered_tools = [
            tool
            for tool in request.tools
            if self._tool_name(tool) in self.allowed_tools
        ]
        return handler(request.override(tools=filtered_tools))

    def wrap_tool_call(self, request, handler):
        tool_name = request.tool_call.get("name", "")
        if tool_name in self.allowed_tools:
            return handler(request)
        return ToolMessage(
            content=f"{self.label}: tool `{tool_name}` is not allowed for this agent role.",
            tool_call_id=request.tool_call["id"],
            name=tool_name,
            status="error",
        )


class FinalOutputGuardMiddleware(AgentMiddleware):
    """Stop non-final follow-up tool calls after declared final artifacts are written."""

    def __init__(self, final_paths: set[str], label: str, completed_paths: set[str] | None = None):
        super().__init__()
        self.final_paths = {_normalize_virtual_path(path) for path in final_paths}
        self.label = label
        self.completed_paths = completed_paths if completed_paths is not None else set()

    def _is_complete(self) -> bool:
        return bool(self.final_paths) and self.final_paths.issubset(self.completed_paths)

    def wrap_tool_call(self, request, handler):
        tool_name = request.tool_call.get("name", "")
        args = request.tool_call.get("args", {})
        if self._is_complete():
            return ToolMessage(
                content=f"{self.label}: final output was already written; stop without extra tool calls.",
                tool_call_id=request.tool_call["id"],
                name=tool_name,
                status="error",
            )

        result = handler(request)
        if tool_name == "write_file":
            path = _normalize_virtual_path(_write_file_path(args))
            if path in self.final_paths:
                self.completed_paths.add(path)
        return result


class RouterToolGuardMiddleware(AgentMiddleware):
    """Keep the main router as a pure delegator."""

    allowed_tools = {"task"}
    router_prompt = """You are Aperio's Main Router. Your only job is task classification and delegation.

Routing rules:
- For code analysis, code health checks, or quality scans, call task and delegate to code-health-orchestrator.
- For PRD writing, PRD review, or product requirements documents, call task and delegate to prd-review-orchestrator.
- If the user asks for both, call task separately for both orchestrators.
- If the request is outside these two task families, briefly say this demo only supports code-health and prd-review.

Hard constraints:
- Use only the task tool for delegation. Do not use ls, glob, grep, read_file, execute, write_file, or internet_search.
- Do not explore files, read source code, search the web, analyze code, write PRDs, or write reports.
- After delegation, wait for the orchestrator result. Do not perform extra validation or file checks.
- If other tools are visible, ignore them."""

    @staticmethod
    def _tool_name(tool) -> str:
        if isinstance(tool, dict):
            function = tool.get("function")
            if isinstance(function, dict) and function.get("name"):
                return str(function["name"])
            if tool.get("name"):
                return str(tool["name"])
            return ""
        return str(getattr(tool, "name", ""))

    def wrap_model_call(self, request, handler):
        # Root router should not see exploration tools. Orchestrators and leaf
        # agents keep their normal tools because this middleware is root-only.
        filtered_tools = [
            tool
            for tool in request.tools
            if self._tool_name(tool) in self.allowed_tools
        ]
        return handler(request.override(
            tools=filtered_tools,
            system_message=SystemMessage(content=self.router_prompt),
        ))

    def wrap_tool_call(self, request, handler):
        tool_name = request.tool_call.get("name", "")
        if tool_name in self.allowed_tools:
            return handler(request)
        return ToolMessage(
            content=(
                "Main Router is a pure routing agent. Direct tool use is blocked here; "
                "delegate to code-health-orchestrator or prd-review-orchestrator with the task tool."
            ),
            tool_call_id=request.tool_call["id"],
            name=tool_name,
            status="error",
        )


class ObservableSummarizationMiddleware(SummarizationMiddleware):
    """DeepAgents summarization with explicit debug and metrics events."""

    def __init__(self, *args, metrics: Metrics, agent_name: str, verbose: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.m = metrics
        self.agent_name = agent_name
        self.verbose = verbose

    def _record_if_summarized(self, request, response) -> None:
        command = getattr(response, "command", None)
        update = getattr(command, "update", None)
        if not isinstance(update, dict):
            return

        summarization_event = update.get("_summarization_event")
        if not isinstance(summarization_event, dict):
            return

        thread_id = PerfMiddleware._thread_id(request)
        messages = getattr(request, "messages", []) or []
        event = {
            "type": "summarization",
            "agent": self.agent_name,
            "thread_id": thread_id,
            "cutoff_index": summarization_event.get("cutoff_index"),
            "file_path": summarization_event.get("file_path"),
            "messages_before": len(messages),
        }
        self.m.summarization_events += 1
        self.m.record_thread(thread_id, "summarization")
        self.m.record_agent(self.agent_name, thread_id, "summarization")
        self.m.events.append(event)
        if self.verbose:
            print(
                "[debug] summarization "
                f"agent={self.agent_name} thread={thread_id} "
                f"cutoff={event['cutoff_index']} file={event['file_path']}"
            )

    def wrap_model_call(self, request, handler):
        response = super().wrap_model_call(request, handler)
        self._record_if_summarized(request, response)
        return response

    async def awrap_model_call(self, request, handler):
        response = await super().awrap_model_call(request, handler)
        self._record_if_summarized(request, response)
        return response


def _observable_summarization_middleware(metrics: Metrics, agent_name: str, model, backend) -> AgentMiddleware:
    defaults = compute_summarization_defaults(model)
    return ObservableSummarizationMiddleware(
        model=model,
        backend=backend,
        trigger=defaults["trigger"],
        keep=defaults["keep"],
        trim_tokens_to_summarize=None,
        truncate_args_settings=defaults["truncate_args_settings"],
        metrics=metrics,
        agent_name=agent_name,
    )


def _agent_middleware(metrics: Metrics, agent_name: str, model, backend) -> list[AgentMiddleware]:
    """Create fresh observability middleware sharing one metrics sink."""
    return [
        _observable_summarization_middleware(metrics, agent_name, model, backend),
        PerfMiddleware(metrics, agent_name=agent_name),
    ]


def _install_observable_summarization_profile(*model_specs: str) -> None:
    """Disable DeepAgents' internal summarizer so this demo can observe it explicitly."""
    for model_spec in dict.fromkeys(spec for spec in model_specs if spec):
        register_harness_profile(
            model_spec,
            HarnessProfile(excluded_middleware=frozenset({"SummarizationMiddleware"})),
        )


# ---------------------------------------------------------------------------
# Sub-agent definitions
# ---------------------------------------------------------------------------

def _code_health_subagents(
    ws: str,
    target: str,
    metrics: Metrics,
    model,
    backend,
    completed_final_paths: set[str],
) -> list:
    """Return the 5 sub-agents for code health orchestration."""
    language_rule = "全文使用中文；工具名、文件路径、命令、错误码可以保留英文原文。"
    evidence_budget = (
        f"证据读取预算：必须先读取 {ws}/raw/tool_results.json；"
        "除非需要复核具体 High/Medium finding 或补足报告必要证据，不要读取源码。"
        "需要读取时只使用 read_file 定向读取 tool_results.json 中出现的具体文件，最多 3 个文件；"
        "不要使用 ls、glob、grep 或 execute 做泛探索。"
    )
    analysis_middleware = lambda agent_name: [
        ToolAllowlistMiddleware({"read_file", "write_file"}, "code-health leaf agent"),
        *_agent_middleware(metrics, agent_name, model, backend),
    ]
    return [
        {
            "name": "architect",
            "description": "Analyze code architecture: directory structure, module coupling, layering",
            "system_prompt": f"""{language_rule}

你是代码架构师。按 code-architect skill 分析 `{target}`。
输入：先读取 {ws}/raw/tool_results.json，必要时读取目标源码。
{evidence_budget}
输出：只写入 Markdown 草稿 {ws}/drafts/architect.md，不要写 JSON/HTML 或别名文件。
完成写入后输出中文摘要并停止。""",
            "skills": _agent_skills(_skill_dir("code-health")),
            "middleware": analysis_middleware("root.code-health-orchestrator.architect"),
        },
        {
            "name": "security-analyst",
            "description": "Audit code for vulnerabilities: SQL injection, XSS, hardcoded secrets, OWASP Top-10",
            "system_prompt": f"""{language_rule}

你是应用安全工程师。按 code-security skill 审计 `{target}`。
输入：先读取 {ws}/raw/tool_results.json，必要时读取目标源码。
{evidence_budget}
输出：只写入 Markdown 草稿 {ws}/drafts/security.md，不要写 JSON/HTML 或别名文件。
完成写入后输出中文摘要并停止。""",
            "skills": _agent_skills(_skill_dir("code-health")),
            "middleware": analysis_middleware("root.code-health-orchestrator.security-analyst"),
        },
        {
            "name": "dependency-checker",
            "description": "Check dependencies: outdated versions, CVEs, license compatibility",
            "system_prompt": f"""{language_rule}

你是依赖管理专家。按 code-dependency skill 检查 `{target}`。
输入：先读取 {ws}/raw/tool_results.json，必要时读取依赖清单。
{evidence_budget}
联网：不要调用 internet_search；本阶段只基于本地扫描事实和草稿证据。
输出：只写入 Markdown 草稿 {ws}/drafts/dependencies.md，不要写 JSON/HTML 或别名文件。
完成写入后输出中文摘要并停止。""",
            "skills": _agent_skills(_skill_dir("code-health")),
            "middleware": analysis_middleware("root.code-health-orchestrator.dependency-checker"),
        },
        {
            "name": "doc-reviewer",
            "description": "Review documentation: README, docstrings, API docs coverage",
            "system_prompt": f"""{language_rule}

你是技术文档专家。按 code-documentation skill 评估 `{target}`。
输入：先读取 {ws}/raw/tool_results.json，必要时读取 README 和目标源码。
{evidence_budget}
输出：只写入 Markdown 草稿 {ws}/drafts/documentation.md，不要写 JSON/HTML 或别名文件。
完成写入后输出中文摘要并停止。""",
            "skills": _agent_skills(_skill_dir("code-health")),
            "middleware": analysis_middleware("root.code-health-orchestrator.doc-reviewer"),
        },
        {
            "name": "summarizer",
            "description": "Merge 4 analysis reports into a consolidated code health report",
            "system_prompt": f"""{language_rule}

你是报告汇总专家。按 report-writing-code-health skill 合并代码健康报告。
输入：读取 {ws}/raw/tool_results.json，以及四个标准草稿 {ws}/drafts/architect.md、{ws}/drafts/security.md、{ws}/drafts/dependencies.md、{ws}/drafts/documentation.md。
输出：只写入 Markdown 最终报告 {ws}/code_health_report.md。
禁止：不要写 HTML/JSON/可视化网页、{ws}/drafts/merged-report.md、/outputs/code_health_report.md 或任何别名文件；不要用 execute 读写 /outputs/ 或做写后验证；不要读取源码或做新的代码探索。
完成写入后立即停止。""",
            "skills": _agent_skills(_skill_dir("code-health")),
            "middleware": [
                ToolAllowlistMiddleware({"read_file", "write_file"}, "code-health summarizer"),
                FinalOutputGuardMiddleware(
                    {f"{ws}/code_health_report.md"},
                    "code-health summarizer",
                    completed_final_paths,
                ),
                *_agent_middleware(metrics, "root.code-health-orchestrator.summarizer", model, backend),
            ],
        },
    ]


def _prd_review_subagents(ws: str, metrics: Metrics, model, backend) -> list:
    """Return the 5 sub-agents for PRD review orchestration (writer is the orchestrator itself)."""
    return [
        {
            "name": "product-strategist",
            "description": "Review PRD from strategy perspective: market, business value, differentiation",
            "system_prompt": f"""你是产品策略分析师。按 review-ops skill 评审 PRD。
输入：读取 {ws}/prd_v1.md。
联网：最多调用 1 次 internet_search，save_path={ws}/raw/web_search/product-strategy.json。
输出：只写入 Markdown 草稿 {ws}/drafts/review_strategy.md，不要写别名文件。
完成写入后输出中文摘要并停止。""",
            "skills": _agent_skills(_skill_dir("prd-review")),
            "middleware": _agent_middleware(metrics, "root.prd-review-orchestrator.product-strategist", model, backend),
        },
        {
            "name": "technical-feasibility",
            "description": "Review PRD from tech perspective: stack, architecture, integration risks",
            "system_prompt": f"""你是技术架构师。按 review-tech skill 评审 PRD。
输入：读取 {ws}/prd_v1.md。
联网：不要调用 internet_search。
输出：只写入 Markdown 草稿 {ws}/drafts/review_tech.md，不要写别名文件。
完成写入后输出中文摘要并停止。""",
            "skills": _agent_skills(_skill_dir("prd-review")),
            "middleware": _agent_middleware(metrics, "root.prd-review-orchestrator.technical-feasibility", model, backend),
        },
        {
            "name": "ux-researcher",
            "description": "Review PRD from UX perspective: user journey, edge cases, accessibility",
            "system_prompt": f"""你是 UX 研究员。按 review-ux skill 评审 PRD。
输入：读取 {ws}/prd_v1.md。
联网：不要调用 internet_search。
输出：只写入 Markdown 草稿 {ws}/drafts/review_ux.md，不要写别名文件。
完成写入后输出中文摘要并停止。""",
            "skills": _agent_skills(_skill_dir("prd-review")),
            "middleware": _agent_middleware(metrics, "root.prd-review-orchestrator.ux-researcher", model, backend),
        },
        {
            "name": "risk-analyst",
            "description": "Review PRD from risk perspective: timeline, resources, privacy, adoption barriers",
            "system_prompt": f"""你是项目风险分析师。按 review-risk skill 评审 PRD。
输入：读取 {ws}/prd_v1.md。
联网：不要调用 internet_search。
输出：只写入 Markdown 草稿 {ws}/drafts/review_risk.md，不要写别名文件。
完成写入后输出中文摘要并停止。""",
            "skills": _agent_skills(_skill_dir("prd-review")),
            "middleware": _agent_middleware(metrics, "root.prd-review-orchestrator.risk-analyst", model, backend),
        },
        {
            "name": "editor",
            "description": "Merge all reviews into a final polished PRD v2 with review matrix",
            "system_prompt": f"""你是 PRD 编辑。按 report-writing-prd 和 review-matrix skills 合并 PRD 评审。
输入：读取 {ws}/prd_v1.md，以及四个标准草稿 {ws}/drafts/review_strategy.md、{ws}/drafts/review_tech.md、{ws}/drafts/review_ux.md、{ws}/drafts/review_risk.md。
输出：分别写入 {ws}/prd_v2_final.md 和 {ws}/review_matrix.md。
禁止：不要接受别名草稿，不要写 final_report.md、merged 文件或 /outputs/*.md；不要用 execute 读写 /outputs/ 或做写后验证。
完成两个文件写入后立即停止。""",
            "skills": _agent_skills(_skill_dir("prd-review")),
            "middleware": _agent_middleware(metrics, "root.prd-review-orchestrator.editor", model, backend),
        },
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    runtime_task = read_runtime_task()
    code_project_path, code_target_rel, code_target_path = resolve_code_health_input(runtime_task)
    code_target_path = (code_project_path if code_target_rel == "." else code_project_path / code_target_rel).resolve()
    sandbox_code_target = (
        SANDBOX_PROJECT_ROOT if code_target_rel == "." else f"{SANDBOX_PROJECT_ROOT}/{code_target_rel}"
    )
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
    if not code_project_path.exists():
        print(f"ERROR: code project not found: {code_project_path}")
        return
    if not code_project_path.is_dir():
        print(f"ERROR: code project must be a directory: {code_project_path}")
        return
    if not code_target_path.exists():
        print(f"ERROR: code target not found: {code_target_path}")
        return

    run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    run_root = WORKSPACE_ROOT / run_id
    code_ws = "/outputs/code_health"
    prd_ws = "/outputs/prd_review"
    local_resources = setup_local_resources(code_target_rel)
    (run_root / "code_health" / "drafts").mkdir(parents=True, exist_ok=True)
    (run_root / "prd_review" / "drafts").mkdir(parents=True, exist_ok=True)

    # ---- Backend (M4 + M5) ----
    store = InMemoryStore()
    sandbox: AgentDockerSandbox | None = None
    try:
        sandbox = AgentDockerSandbox(code_project_path, SKILLS_DIR, run_root)
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

    custom_tools = _load_mcp_tools(run_root)

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
    _install_observable_summarization_profile(primary_model_name, fallback_model_name)

    # ---- Middleware ----
    metrics = Metrics()
    root_observability = _agent_middleware(metrics, "root", model, backend)
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
        RouterToolGuardMiddleware(),
        *root_observability,
        ToolRetryMiddleware(
            tools=["ls", "glob", "grep", "read_file", "internet_search"],
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
    root_interrupt_on = {
        "execute": {
            "allowed_decisions": ["approve", "reject"],
            "when": _execute_requires_approval,
        },
        "write_file": {
            "allowed_decisions": ["approve", "reject"],
            "when": _write_requires_approval,
        },
    }
    code_health_final_paths: set[str] = set()

    code_health_orchestrator = {
        "name": "code-health-orchestrator",
        "description": "Orchestrate a code health scan: spawn 4 parallel analysis sub-agents then merge results",
        "skills": _agent_skills(_skill_dir("code-health")),
        "middleware": [
            ToolAllowlistMiddleware({"execute", "read_file", "task", "write_todos"}, "code-health orchestrator"),
            FinalOutputGuardMiddleware(
                {f"{code_ws}/code_health_report.md"},
                "code-health orchestrator",
                code_health_final_paths,
            ),
            *_agent_middleware(metrics, "root.code-health-orchestrator", model, backend),
        ],
        "system_prompt": f"""你是代码健康检查编排器（Code Health Orchestrator）。

第一动作硬约束：
1. 收到 code-health 任务后，先按 code-health-toolkit skill 运行确定性扫描，并把完整原始结果写入 {code_ws}/raw/tool_results.json。
   扫描入口、project-root、target 和参数都由 code-health-toolkit skill、用户任务和本地策略确定；主编排器不要写死脚本路径或目标路径。
2. 在 {code_ws}/raw/tool_results.json 生成之前，禁止调用 write_todos、task、ls、read_file、glob、grep、internet_search 或任何源码探索工具。
3. 不要手写 ruff/mypy/bandit/radon/detect-secrets/deptry/interrogate 等单独命令替代 code-health-toolkit。
4. 扫描成功后，先读取 {code_ws}/raw/tool_results.json；再使用 write_todos 规划后续编排。

扫描后的工作流程：
1. 可读取 /local-resources/aperio_policy.yaml 了解安全和存储策略。
2. 必须使用 task 工具并行派发 4 个已声明子代理分析 `{sandbox_code_target}`，并要求它们优先引用 {code_ws}/raw/tool_results.json 中的事实：
   - architect：架构分析
   - security-analyst：安全审计
   - dependency-checker：依赖检查
   - doc-reviewer：文档评估
   每个子代理将结果写入 {code_ws}/drafts/
3. 等待全部 4 个完成后，派发 summarizer 合并生成最终报告。
4. summarizer 的唯一最终产物必须是 Markdown 文件 {code_ws}/code_health_report.md；不要接受 HTML、JSON、/outputs/*.md 或 {code_ws}/drafts/merged-report.md 作为最终报告。
5. summarizer 写入最终报告后流程结束；不要再使用 execute 验证、复制、重写或另存 /outputs/ 中的文件。
6. 如有 /memories/history/ 中的历史数据，可在扫描完成后进行趋势对比。

硬性约束：
- code-health 的 4 个草稿和最终报告必须全文使用中文，包括标题、表头、段落、结论和建议；工具名、文件路径、命令、错误码可以保留英文原文
- internet_search 是只读联网工具，只能作为公开资料补充；代码事实、扫描结论和最终风险判定必须优先来自本地代码、{code_ws}/raw/tool_results.json 和草稿报告
- 如果引用 internet_search 结果，必须明确标注其为公开网络证据，并保留链接；联网失败时不要编造公开资料
- orchestrator 自己不要在扫描后泛读源码；源码阅读由叶子子代理按工具发现的位置少量定向完成
- 必须先得到这 4 个草稿文件：{code_ws}/drafts/architect.md、{code_ws}/drafts/security.md、{code_ws}/drafts/dependencies.md、{code_ws}/drafts/documentation.md
- 这 4 个草稿必须由对应叶子子代理写入；code-health-orchestrator 自己不得调用 write_file 代写 {code_ws}/drafts/*.md
- 派发子代理时必须使用这些精确名称：architect、security-analyst、dependency-checker、doc-reviewer；不要创建临时分析角色、不要把多个维度合并给同一个子代理
- 缺少任意一个草稿文件时，不要派发 summarizer，不要直接写最终报告
- 不要写入 {code_ws}/drafts/code_health_report.md；drafts 目录只放四个角色草稿
- 不要写入 /outputs/code_health_report.md；最终报告只能是 {code_ws}/code_health_report.md""",
        "subagents": _code_health_subagents(
            code_ws,
            sandbox_code_target,
            metrics,
            model,
            backend,
            code_health_final_paths,
        ),
    }
    prd_review_orchestrator = {
        "name": "prd-review-orchestrator",
        "description": "Orchestrate PRD review: write a PRD, spawn 4 parallel reviewers, then editor merges feedback",
        "skills": _agent_skills(_skill_dir("prd-review")),
        "middleware": _agent_middleware(metrics, "root.prd-review-orchestrator", model, backend),
        "system_prompt": f"""你是 PRD 评审编排器（PRD Review Orchestrator）。工作流程：

1. 可先读取 /local-resources/aperio_policy.yaml 了解安全和存储策略
2. 作为 Writer，按 prd-writing skill 编写 PRD 初稿
3. 必须先且仅调用 1 次 internet_search，将公开证据保存到 {prd_ws}/raw/web_search/writer-research.json；不要保存到其他 web_search 文件名
4. 根据用户需求和公开证据写入唯一 PRD 初稿 {prd_ws}/prd_v1.md；引用联网信息时必须标注“公开网络证据”，但不要把搜索摘要当作用户已经确认的需求
5. 并行派发 4 个评审子代理：
   - product-strategist：产品策略
   - technical-feasibility：技术可行性
   - ux-researcher：用户体验
   - risk-analyst：风险评估
   每个子代理读取 {prd_ws}/prd_v1.md，将评审写入固定文件：
   - product-strategist → {prd_ws}/drafts/review_strategy.md，最多 1 次 internet_search，save_path={prd_ws}/raw/web_search/product-strategy.json
   - technical-feasibility → {prd_ws}/drafts/review_tech.md，不要调用 internet_search
   - ux-researcher → {prd_ws}/drafts/review_ux.md，不要调用 internet_search
   - risk-analyst → {prd_ws}/drafts/review_risk.md，不要调用 internet_search
6. 等待全部 4 个完成后，派发 editor 合并生成 PRD v2 + 评审矩阵
7. editor 的最终产物必须拆分为 {prd_ws}/prd_v2_final.md 和 {prd_ws}/review_matrix.md
8. 上述两个文件写入后流程即完成，不要再创建根目录 /outputs/*.md、别名文件、merged 文件或 {prd_ws}/final_report.md
9. 不要再使用 execute 验证、复制、重写或另存 /outputs/ 中的文件

硬约束：
- PRD 评审草稿只能是上述四个文件名；不要创建 review-product-completeness.md、review-technical-feasibility.md、review-ux.md、review-risk.md 或任何角色名别名文件
- PRD 联网搜索预算只有两个保存路径：{prd_ws}/raw/web_search/writer-research.json 和 {prd_ws}/raw/web_search/product-strategy.json，各最多 1 次

PRD 使用中文撰写。""",
        "subagents": _prd_review_subagents(prd_ws, metrics, model, backend),
    }
    code_health_orchestrator["runnable"] = create_deep_agent(
        model=model,
        tools=custom_tools,
        backend=backend,
        checkpointer=checkpointer,
        middleware=code_health_orchestrator["middleware"],
        permissions=permissions,
        skills=code_health_orchestrator["skills"],
        interrupt_on=root_interrupt_on,
        system_prompt=code_health_orchestrator["system_prompt"],
        subagents=code_health_orchestrator["subagents"],
        name=code_health_orchestrator["name"],
    )
    prd_review_orchestrator["runnable"] = create_deep_agent(
        model=model,
        tools=custom_tools,
        backend=backend,
        checkpointer=checkpointer,
        middleware=prd_review_orchestrator["middleware"],
        permissions=permissions,
        skills=prd_review_orchestrator["skills"],
        interrupt_on=root_interrupt_on,
        system_prompt=prd_review_orchestrator["system_prompt"],
        subagents=prd_review_orchestrator["subagents"],
        name=prd_review_orchestrator["name"],
    )
    subagent_specs = [code_health_orchestrator, prd_review_orchestrator]

    print_backend_debug(run_root, local_resources, sandbox)
    print_sandbox_tool_debug(sandbox)
    print_subagent_debug(subagent_specs, backend)
    print_tool_debug(subagent_specs, custom_tools)
    print_subagent_middleware_debug(subagent_specs)
    print_middleware_debug(user_middleware)

    # ---- Main Router Agent ----
    agent = create_deep_agent(
        model=model,
        tools=custom_tools,
        backend=backend,
        checkpointer=checkpointer,
        middleware=user_middleware,
        permissions=permissions,
        skills=_agent_skills(),
        interrupt_on=root_interrupt_on,
        system_prompt="""你是 Aperio 研发质量平台的**任务路由器**。根据用户输入判断任务类型：

- 如果用户要求**分析代码、代码体检、代码健康检查**→ 委托给 code-health-orchestrator
- 如果用户要求**写 PRD、评审需求、产品需求文档**→ 委托给 prd-review-orchestrator
- 如果用户同时要求两类任务，分别委托给两个 Orchestrator
- 如果用户请求不属于上述两类，简短说明当前 demo 只支持 code-health 和 prd-review

你的职责只是识别任务类型并路由到正确的 Orchestrator。不要自己调用工具、读取文件、联网搜索、分析代码、撰写 PRD 或写入报告。""",
        subagents=subagent_specs,
    )

    # ---- Run: Code Health ----
    print(f"\n{'─' * 60}")
    print("Task 1: Code Health Scan")
    print(f"Project: {code_project_path}")
    print(f"Target:  {code_target_rel}")
    print(f"User:    {runtime_task}")
    print(f"{'─' * 60}")

    t0 = time.time()
    code_config = {"configurable": {"thread_id": f"{run_id}:code-health"}}
    resp = agent.invoke(
        {
            "messages": [{
                "role": "user",
                "content": (
                    f"{runtime_task}\n\n"
                    "运行时解析信息："
                    "代码项目已挂载到沙盒 `/workspace/project`；"
                    f"扫描目标相对项目根路径是 `{code_target_rel}`；"
                    "原始工具结果必须写入 `/outputs/code_health/raw/tool_results.json`。"
                ),
            }],
        },
        config=code_config,
        version="v2",
    )
    resp = handle_human_approval(agent, resp, code_config, "code-health")
    t_code = time.time() - t0

    # ---- Run: PRD Review ----
    # print(f"\n{'─' * 60}")
    # print("Task 2: PRD Review")
    # print(f"{'─' * 60}")

    # t1 = time.time()
    # prd_config = {"configurable": {"thread_id": f"{run_id}:prd-review"}}
    # resp = agent.invoke(
    #     {
    #         "messages": [{
    #             "role": "user",
    #             "content": (
    #                 "我需要为「智慧校园导航助手」写一份 PRD 并评审。"
    #                 "这是一款基于 AR/语音的校内导航 App，帮助新生和访客快速找到教室和设施，"
    #                 "同时提供校园活动推荐和实时拥挤度信息。"
    #                 "写入标准 PRD v2 和评审矩阵。"
    #             ),
    #         }],
    #     },
    #     config=prd_config,
    #     version="v2",
    # )
    # resp = handle_human_approval(agent, resp, prd_config, "prd-review")
    # t_prd = time.time() - t1

    # ---- Output ----
    print(f"\n{'=' * 60}")

    # Performance
    m = metrics.to_dict()
    print(f"📊 Performance:")
    print(f"   Code Health:   {t_code:.1f}s")
    # print(f"   PRD Review:    {t_prd:.1f}s")
    # print(f"   Total wall:    {t_code + t_prd:.1f}s")
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
