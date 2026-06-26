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
import shutil
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
TARGET_CODE = "full-stack-fastapi-template-master/backend/app/core"
SANDBOX_TARGET_CODE = "/workspace/code"


def _skill_dir(name: str) -> str:
    """Build a virtual skill source routed through CompositeBackend."""
    return "/skills/" + name.replace("\\", "/").strip("/")


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
storage:
  default: docker_sandbox
  outputs: filesystem
  temp: state
  memories: store
  local_resources: read_only_filesystem
code_health:
  source_path: /workspace/code
  report_path: /outputs/code_health
prd_review:
  report_path: /outputs/prd_review
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
    print(f"  default: DockerSandbox container={sandbox.id[:12] if sandbox.id != 'closed' else 'closed'}")
    print(f"  /outputs/ -> FilesystemBackend({run_root})")
    print("  /memories/ -> StoreBackend(namespace=aperio)")
    print("  /temp/ -> StateBackend()")
    print(f"  /local-resources/ -> FilesystemBackend({local_resources}) [write denied]")
    print(f"  /skills/ -> FilesystemBackend({SKILLS_DIR})")


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


def print_middleware_debug(middleware: list[AgentMiddleware]) -> None:
    print("\n[debug] User-configured middleware")
    print("  note: DeepAgents also installs TodoList, Filesystem, SubAgent, Summarization, and HITL middleware internally.")
    print("  model rate limit: 10 requests/minute shared by primary and fallback models")
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

    alternates = [
        run_root / "code_health_report.md",
        run_root / "campus_nav_prd.md",
        run_root / "campus_nav_review_report.md",
        run_root / "smart_campus_navigator_prd_report.md",
    ]
    found_alternates = [path for path in alternates if path.exists() and path.stat().st_size > 0]
    if found_alternates:
        print("  note: found root-level alternate outputs:")
        for path in found_alternates:
            print(f"        {path.relative_to(run_root)} ({path.stat().st_size} bytes)")


def _is_root_output_path(path: str) -> bool:
    normalized = path.replace("\\", "/").strip().strip("'\"")
    if not normalized.startswith("/outputs/"):
        return False
    relative = normalized[len("/outputs/"):].strip("/")
    return bool(relative) and "/" not in relative


def _command_mentions_root_output(command: str) -> bool:
    for match in re.findall(r"/outputs/[^\s;&|<>]+", command):
        if _is_root_output_path(match):
            return True
    return False


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
                    if _command_mentions_root_output(command):
                        print("     自动拒绝: 禁止写入 /outputs/ 根目录，请改写到 /outputs/code_health/ 或 /outputs/prd_review/")
                        decisions.append({
                            "action_id": action.get("id"),
                            "tool_name": action["name"],
                            "type": "reject",
                            "updated_args": None,
                        })
                        continue
                elif action['name'] == 'write_file':
                    file_path = (
                        action['args'].get('file_path')
                        or action['args'].get('path')
                        or action['args'].get('filename')
                        or action['args'].get('name')
                        or 'N/A'
                    )
                    print(f"     文件: {file_path}")
                    content = action['args'].get('content', '')
                    print(f"     内容: {str(content)[:200]}...")
                    if _is_root_output_path(file_path):
                        print("     自动拒绝: 禁止写入 /outputs/ 根目录，请改写到 /outputs/code_health/ 或 /outputs/prd_review/")
                        decisions.append({
                            "action_id": action.get("id"),
                            "tool_name": action["name"],
                            "type": "reject",
                            "updated_args": None,
                        })
                        continue
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


def _copy_if_missing(source: Path, target: Path, run_root: Path) -> bool:
    if target.exists() and target.stat().st_size > 0:
        return False
    if not source.exists() or source.stat().st_size == 0:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    print(f"[debug] normalized output {source.relative_to(run_root)} -> {target.relative_to(run_root)}")
    return True


def _extract_section(source: Path, target: Path, heading_keyword: str, run_root: Path) -> bool:
    if target.exists() and target.stat().st_size > 0:
        return False
    if not source.exists() or source.stat().st_size == 0:
        return False
    lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    start = next((i for i, line in enumerate(lines) if heading_keyword in line), None)
    if start is None:
        return False
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("# ") and heading_keyword not in lines[i]:
            end = i
            break
    content = "\n".join(lines[start:end]).strip() + "\n"
    if not content.strip():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(f"[debug] extracted {heading_keyword} -> {target.relative_to(run_root)}")
    return True


def normalize_final_outputs(run_root: Path) -> None:
    """Normalize model-chosen output aliases into stable demo contract paths."""
    _copy_if_missing(
        run_root / "code_health" / "report.md",
        run_root / "code_health" / "code_health_report.md",
        run_root,
    )

    prd_final = run_root / "prd_review" / "final_report.md"
    _copy_if_missing(prd_final, run_root / "prd_review" / "prd_v2_final.md", run_root)
    _extract_section(prd_final, run_root / "prd_review" / "review_matrix.md", "评审矩阵", run_root)


# ---------------------------------------------------------------------------
# Docker Sandbox (Exercise 12 pattern)
# ---------------------------------------------------------------------------

class DockerSandbox:
    """DeepAgents-compatible Docker backend based on Exercise 12."""

    def __init__(
        self,
        image: str = "python:3.11-slim",
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
        self.container = self.client.containers.run(
            self.image,
            command="tail -f /dev/null",
            detach=True,
            working_dir=self.working_dir,
            remove=self.remove_on_close,
        )
        print(f"Docker sandbox: started {self.container.id[:12]} ({self.image})")

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

    def execute(self, command: str) -> tuple[str, int]:
        if not self.container:
            raise RuntimeError("sandbox container is not running")
        exit_code, output = self.container.exec_run(
            ["sh", "-c", command],
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

    def __init__(self, target_code: Path) -> None:
        self._sandbox = DockerSandbox()
        self._sandbox.seed_directory(target_code, SANDBOX_TARGET_CODE)

    @property
    def id(self) -> str:
        return self._sandbox.container.id if self._sandbox.container else "closed"

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        output, exit_code = self._sandbox.execute(command)
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
    return [
        {
            "name": "architect",
            "description": "Analyze code architecture: directory structure, module coupling, layering",
            "system_prompt": f"""你是代码架构师。分析 `{target}` 的架构：
- 目录结构是否合理、模块粒度是否合适
- 是否存在循环依赖、God Class
- 分层是否清晰（API → 业务 → 数据）
将分析结果写入 {ws}/drafts/architect.md，最后输出中文摘要。""",
            "skills": [_skill_dir("code-health/code-architect")],
        },
        {
            "name": "security-analyst",
            "description": "Audit code for vulnerabilities: SQL injection, XSS, hardcoded secrets, OWASP Top-10",
            "system_prompt": f"""你是应用安全工程师。审计 `{target}` 的安全：
- SQL 注入、XSS、硬编码密钥
- 不安全反序列化、路径遍历
- 敏感端点认证缺失
将分析结果写入 {ws}/drafts/security.md，最后输出中文摘要。""",
            "skills": [_skill_dir("code-health/code-security")],
        },
        {
            "name": "dependency-checker",
            "description": "Check dependencies: outdated versions, CVEs, license compatibility",
            "system_prompt": f"""你是依赖管理专家。检查 `{target}` 的依赖：
- 主版本落后的包、已知 CVE
- 许可证兼容性、未声明的传递依赖
将分析结果写入 {ws}/drafts/dependencies.md，最后输出中文摘要。""",
            "skills": [_skill_dir("code-health/code-dependency")],
        },
        {
            "name": "doc-reviewer",
            "description": "Review documentation: README, docstrings, API docs coverage",
            "system_prompt": f"""你是技术文档专家。评估 `{target}` 的文档：
- README 完整性、公开 API docstring 覆盖率
- 注释质量、配置说明
将分析结果写入 {ws}/drafts/documentation.md，最后输出中文摘要。""",
            "skills": [_skill_dir("code-health/code-documentation")],
        },
        {
            "name": "summarizer",
            "description": "Merge 4 analysis reports into a consolidated code health report",
            "system_prompt": f"""你是报告汇总专家。任务：
1. ls {ws}/drafts/ 发现所有分析报告
2. read_file 逐个读取
3. 去重合并 → 按严重度分级（Critical/High/Medium/Low）
4. 计算健康度评分（0-100）
5. 必须调用 write_file 工具，将最终报告写入这个唯一最终路径：{ws}/code_health_report.md

报告使用中文，包含执行摘要和趋势对比（如有历史数据）。
最终输出契约：
- 最终报告只能写入 {ws}/code_health_report.md
- 不要写入 /outputs/code_health_report.md 或其他根级别文件名
- 完成前请确认 {ws}/code_health_report.md 已经写入，不要只在最终消息中总结。""",
            "skills": [_skill_dir("general/report-writing")],
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
读取 {ws}/prd_v1.md，将评审写入 {ws}/drafts/review_strategy.md，输出中文摘要。""",
            "skills": [_skill_dir("prd-review/review-ops")],
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
- 不要写入 /outputs/campus_nav_prd.md、/outputs/campus_nav_review_report.md 或其他根级别文件名
- 完成前请确认 {ws}/prd_v2_final.md 和 {ws}/review_matrix.md 已经写入，不要只在最终消息中总结。""",
            "skills": [_skill_dir("general/report-writing"), _skill_dir("general/review-matrix")],
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
    target_path = (_PROJECT_ROOT / TARGET_CODE).resolve()
    if not target_path.exists():
        print(f"ERROR: target '{TARGET_CODE}' not found")
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
        sandbox = AgentDockerSandbox(target_path)
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
            tools=["ls", "glob", "grep", "read_file"],
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
2. 可先读取 /local-resources/aperio_policy.yaml 了解安全和存储策略
3. 并行派发 4 个子代理分析 {SANDBOX_TARGET_CODE}：
   - architect：架构分析
   - security-analyst：安全审计
   - dependency-checker：依赖检查
   - doc-reviewer：文档评估
   每个子代理将结果写入 {code_ws}/drafts/
4. 等待全部 4 个完成后，派发 summarizer 合并生成最终报告
5. 如有 /memories/history/ 中的历史数据，进行趋势对比""",
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

PRD 使用中文撰写。""",
        "subagents": _prd_review_subagents(prd_ws),
    }
    subagent_specs = [code_health_orchestrator, prd_review_orchestrator]

    print_backend_debug(run_root, local_resources, sandbox)
    print_subagent_debug(subagent_specs, backend)
    print_middleware_debug(user_middleware)

    # ---- Main Router Agent ----
    agent = create_deep_agent(
        model=model,
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
                    "请使用 prd-review-orchestrator 执行全流程，并写入报告。"
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

    normalize_final_outputs(run_root)

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
